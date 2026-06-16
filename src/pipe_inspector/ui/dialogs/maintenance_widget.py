"""
maintenance_widget.py
─────────────────────
Maintenance / Calibration tab with live feed + Detect Zone drift monitor.

Flow:
  1. User sees live camera feed
  2. Click "Define Zone" → click 4 points → quadrilateral defined
  3. Click "Save Reference" → snapshot current zone pixels as reference
  4. Every frame: MSE diff between current zone vs reference
  5. If diff > threshold → status = "ZONE DRIFTED"

Persistence (QSettings):
  calibration/zone_points  : "x1,y1;x2,y2;x3,y3;x4,y4"
  calibration/threshold    : int (0-100)
  (reference frame saved as data/calibration_ref.npy)
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from PySide6.QtCore import Qt, QSettings, Signal, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from pipe_inspector import paths

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
REF_FRAME_PATH = paths.DATA_DIR / "calibration_ref.npy"
DEFAULT_THRESHOLD = 30    # MSE threshold (0-100 slider range)
DRIFT_CHECK_EVERY = 5     # compute drift every N frames (save CPU)


# ══════════════════════════════════════════════════════════════════════════
# ClickableFrameView — QWidget ที่รับ click + แสดงภาพ + overlay
# ══════════════════════════════════════════════════════════════════════════

class ClickableFrameView(QWidget):
    """
    QWidget ที่:
      - แสดง BGR frame (letterboxed, keep aspect)
      - รับ mouse click → emit ใน image-coordinate (ไม่ใช่ widget coord)
      - วาด zone overlay ตาม 4 จุดที่ถูก set
    """
    image_clicked = Signal(int, int)   # (image_x, image_y)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._image_size: tuple[int, int] | None = None   # (w, h)
        self._zone_points: list[tuple[int, int]] = []        # in image coords
        self._collecting: bool = False
        self._drift_color: QColor = QColor("#00e676")        # green=stable, red=drifted
        self._last_scaled_info = (0, 0, 0, 0, 1.0, 1.0)      # x_off, y_off, w, h, sx, sy

        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CrossCursor)

    # ── Public ─────────────────────────────────────────────────────────────

    def set_frame(self, frame_bgr: np.ndarray) -> None:
        h, w = frame_bgr.shape[:2]
        self._image_size = (w, h)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        qimg = QImage(frame_rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg.copy())   # .copy() since frame buffer reused
        self.update()

    def set_zone(self, points: list[tuple[int, int]]) -> None:
        self._zone_points = list(points)
        self.update()

    def set_collecting(self, collecting: bool) -> None:
        self._collecting = collecting
        self.setCursor(Qt.CrossCursor if collecting else Qt.ArrowCursor)

    def set_drift_color(self, color: QColor) -> None:
        self._drift_color = color
        self.update()

    # ── Mouse handling ─────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if not self._collecting or self._image_size is None or self._pixmap is None:
            return
        x_off, y_off, sw, sh, _sx, _sy = self._last_scaled_info
        mx = event.position().x() - x_off
        my = event.position().y() - y_off
        if not (0 <= mx <= sw and 0 <= my <= sh):
            return   # click outside the image
        img_w, img_h = self._image_size
        img_x = int(mx * img_w / sw)
        img_y = int(my * img_h / sh)
        self.image_clicked.emit(img_x, img_y)

    # ── Paint ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect()
        p.fillRect(rect, QColor("#1a1d23"))

        if self._pixmap is None:
            p.setPen(QColor("#a8b0ba"))
            p.setFont(QFont("Segoe UI", 16))
            p.drawText(rect, Qt.AlignCenter, "รอ live feed จากกล้อง…")
            return

        scaled = self._pixmap.scaled(
            rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        x_off = (rect.width()  - scaled.width())  // 2
        y_off = (rect.height() - scaled.height()) // 2
        p.drawPixmap(x_off, y_off, scaled)

        # save scale info for click mapping
        if self._image_size:
            iw, ih = self._image_size
            sx = scaled.width()  / iw
            sy = scaled.height() / ih
            self._last_scaled_info = (x_off, y_off, scaled.width(), scaled.height(), sx, sy)
            # ── Draw zone overlay ──────────────────────────────────────────
            if self._zone_points:
                self._draw_zone(p, x_off, y_off, sx, sy)

    def _draw_zone(self, p: QPainter, x_off: int, y_off: int, sx: float, sy: float) -> None:
        # Polygon lines — thicker for factory visibility
        pen = QPen(self._drift_color, 3, Qt.SolidLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.setFont(QFont("Segoe UI", 12, QFont.Bold))

        pts_widget = [
            (int(x * sx) + x_off, int(y * sy) + y_off)
            for (x, y) in self._zone_points
        ]
        # connect consecutive points; close if 4
        for i, (px, py) in enumerate(pts_widget):
            # vertex marker (bigger for touch visibility)
            p.setBrush(self._drift_color)
            p.drawEllipse(px - 8, py - 8, 16, 16)
            # number label with white outline halo for readability
            p.setPen(QColor("#ffffff"))
            p.drawText(px + 12, py - 10, str(i + 1))
            p.setPen(pen)
            # line to next
            if i + 1 < len(pts_widget):
                p.setBrush(Qt.NoBrush)
                nx, ny = pts_widget[i + 1]
                p.drawLine(px, py, nx, ny)
        # close the polygon once 4 points
        if len(pts_widget) == 4:
            p.setBrush(Qt.NoBrush)
            p.drawLine(*pts_widget[3], *pts_widget[0])


# ══════════════════════════════════════════════════════════════════════════
# MaintenanceWidget — tab content
# ══════════════════════════════════════════════════════════════════════════

class MaintenanceWidget(QWidget):
    """Tab for camera calibration + drift monitoring."""

    STATUS_IDLE      = "idle"
    STATUS_COLLECT   = "collect"
    STATUS_STABLE    = "stable"
    STATUS_DRIFTED   = "drifted"
    STATUS_NO_REF    = "no_reference"

    # emit เมื่อ drift status เปลี่ยน (transition เท่านั้น ไม่ emit ทุก frame)
    # str  = status constant ("stable" / "drifted" / ...)
    # float = drift score ณ ขณะนั้น (0.0 ถ้า stable/reset)
    drift_status_changed = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings()
        self._zone_points: list[tuple[int, int]] = []
        self._reference_gray: np.ndarray | None = None   # masked gray ref
        self._mask: np.ndarray | None = None              # binary mask for zone
        self._latest_frame: np.ndarray | None = None
        self._threshold: int = DEFAULT_THRESHOLD
        self._status = self.STATUS_IDLE
        self._frame_counter: int = 0
        self._last_drift: float = 0.0

        self._build_ui()
        self._load_persisted()
        self._refresh_status()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Left: live view
        self._view = ClickableFrameView()
        self._view.image_clicked.connect(self._on_image_click)
        root.addWidget(self._view, stretch=1)

        # Right: controls panel
        panel = QFrame()
        panel.setObjectName("maintenancePanel")
        panel.setFixedWidth(400)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(20, 20, 20, 20)
        pl.setSpacing(14)

        # Heading
        heading = QLabel("MAINTENANCE")
        heading.setObjectName("maintHeading")
        f = QFont("Segoe UI", 16, QFont.Bold)
        heading.setFont(f)
        pl.addWidget(heading)

        # Instructions
        self._instruct = QLabel("")
        self._instruct.setWordWrap(True)
        self._instruct.setObjectName("instructLabel")
        pl.addWidget(self._instruct)

        pl.addSpacing(8)

        # Define / Clear zone
        self._btn_define = QPushButton("Define Zone (4 points)")
        self._btn_define.setObjectName("primaryBtn")
        self._btn_define.setFixedHeight(52)
        self._btn_define.clicked.connect(self._start_collecting)
        pl.addWidget(self._btn_define)

        self._btn_clear = QPushButton("Clear Zone")
        self._btn_clear.setObjectName("secondaryBtn")
        self._btn_clear.setFixedHeight(44)
        self._btn_clear.clicked.connect(self._clear_zone)
        pl.addWidget(self._btn_clear)

        pl.addSpacing(12)

        # Save reference
        self._btn_save_ref = QPushButton("Save Reference")
        self._btn_save_ref.setObjectName("successBtn")
        self._btn_save_ref.setFixedHeight(52)
        self._btn_save_ref.clicked.connect(self._save_reference)
        pl.addWidget(self._btn_save_ref)

        pl.addSpacing(12)

        # Threshold slider
        t_label = QLabel("Threshold")
        t_label.setObjectName("sliderLabel")
        pl.addWidget(t_label)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(1, 100)
        self._slider.setValue(self._threshold)
        self._slider.setFixedHeight(36)
        self._slider.valueChanged.connect(self._on_threshold_changed)
        slider_row.addWidget(self._slider, stretch=1)

        self._threshold_label = QLabel(str(self._threshold))
        self._threshold_label.setObjectName("thresholdValue")
        self._threshold_label.setFixedWidth(48)
        self._threshold_label.setAlignment(Qt.AlignCenter)
        slider_row.addWidget(self._threshold_label)
        pl.addLayout(slider_row)

        pl.addSpacing(16)

        # Status indicator
        self._status_box = QFrame()
        self._status_box.setObjectName("statusBox")
        self._status_box.setFixedHeight(120)
        sl = QVBoxLayout(self._status_box)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(6)

        self._status_title = QLabel("STATUS")
        self._status_title.setObjectName("statusTitle")
        sl.addWidget(self._status_title)

        self._status_text = QLabel("—")
        self._status_text.setObjectName("statusValue")
        self._status_text.setFont(QFont("Segoe UI", 15, QFont.Bold))
        sl.addWidget(self._status_text)

        self._drift_value = QLabel("")
        self._drift_value.setObjectName("driftValue")
        sl.addWidget(self._drift_value)

        pl.addWidget(self._status_box)

        pl.addStretch(1)
        root.addWidget(panel)

        self._apply_stylesheet()

    def _apply_stylesheet(self) -> None:
        """Light industrial theme — matches main_window.py palette."""
        self.setStyleSheet("""
            #maintenancePanel {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 8px;
            }
            #maintHeading {
                color: #1a1d23;
                letter-spacing: 1px;
            }
            #instructLabel {
                color: #52606d;
                font-size: 13px;
                line-height: 1.5;
            }
            #sliderLabel {
                color: #52606d;
                font-size: 12px;
                font-weight: bold;
                text-transform: uppercase;
                letter-spacing: 1.5px;
            }
            #thresholdValue {
                font-family: "Consolas", monospace;
                font-size: 17px;
                font-weight: bold;
                color: #1565c0;
                background: #e3f2fd;
                border: 1px solid #1565c0;
                border-radius: 6px;
                padding: 4px 6px;
            }
            #statusBox {
                background: #f7f8fa;
                border: 2px solid #cbd1d9;
                border-radius: 8px;
            }
            /* label ใน statusBox โปร่งใส — ไม่ให้พื้นขาว global ขึ้นเป็นกล่อง */
            #statusBox QLabel { background: transparent; }
            #statusTitle {
                color: #52606d;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 2px;
            }
            #statusValue { color: #1a1d23; }
            #driftValue  {
                color: #52606d;
                font-size: 12px;
                font-family: "Consolas", monospace;
            }

            /* Button palette — defined canonically in MainWindow QSS; no overrides here */

            /* Slider — bigger handle for touch */
            QSlider::groove:horizontal {
                background: #cbd1d9; height: 8px; border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #1565c0;
                border: 2px solid #0d47a1;
                width: 28px; height: 28px;
                margin: -12px 0; border-radius: 14px;
            }
            QSlider::handle:horizontal:hover { background: #1976d2; }
            QSlider::sub-page:horizontal {
                background: #1565c0; border-radius: 4px;
            }

            QLabel { color: #1a1d23; font-size: 14px; }
        """)

    # ── Persisted state ────────────────────────────────────────────────────

    def _load_persisted(self) -> None:
        # Zone points
        raw = self._settings.value("calibration/zone_points", "", type=str)
        if raw:
            try:
                pts = []
                for pair in raw.split(";"):
                    x, y = pair.split(",")
                    pts.append((int(x), int(y)))
                if len(pts) == 4:
                    self._zone_points = pts
                    self._view.set_zone(pts)
                    logger.info(f"Maintenance: loaded persisted zone {pts}")
            except ValueError as exc:
                logger.warning(f"Maintenance: failed to parse zone: {exc}")

        # Threshold
        self._threshold = int(self._settings.value("calibration/threshold", DEFAULT_THRESHOLD))
        self._slider.setValue(self._threshold)
        self._threshold_label.setText(str(self._threshold))

        # Reference frame
        if REF_FRAME_PATH.exists():
            try:
                self._reference_gray = np.load(REF_FRAME_PATH)
                logger.info(f"Maintenance: loaded reference from {REF_FRAME_PATH}")
            except (OSError, ValueError) as exc:
                logger.warning(f"Maintenance: failed to load reference: {exc}")

    def _save_zone_to_settings(self) -> None:
        raw = ";".join(f"{x},{y}" for x, y in self._zone_points)
        self._settings.setValue("calibration/zone_points", raw)

    # ── Frame hookup (called from MainWindow) ──────────────────────────────

    @Slot(object)
    def on_frame(self, frame_bgr: np.ndarray) -> None:
        """Called by MainWindow — forward camera frame to maintenance view."""
        self._latest_frame = frame_bgr
        self._view.set_frame(frame_bgr)

        # Drift detection (throttled)
        self._frame_counter += 1
        if self._frame_counter % DRIFT_CHECK_EVERY == 0:
            self._check_drift(frame_bgr)

    # ── Zone definition ────────────────────────────────────────────────────

    def _start_collecting(self) -> None:
        self._zone_points = []
        self._view.set_zone([])
        self._view.set_collecting(True)
        self._status = self.STATUS_COLLECT
        self._refresh_status()
        logger.info("Maintenance: collecting 4 zone points...")

    def _clear_zone(self) -> None:
        self._zone_points = []
        self._reference_gray = None
        self._mask = None
        self._view.set_zone([])
        self._view.set_collecting(False)
        self._settings.remove("calibration/zone_points")
        if REF_FRAME_PATH.exists():
            try:
                REF_FRAME_PATH.unlink()
            except Exception:
                pass
        self._status = self.STATUS_IDLE
        self._refresh_status()
        logger.info("Maintenance: zone cleared.")

    @Slot(int, int)
    def _on_image_click(self, x: int, y: int) -> None:
        if self._status != self.STATUS_COLLECT:
            return
        self._zone_points.append((x, y))
        self._view.set_zone(self._zone_points)
        logger.info(f"Maintenance: zone point {len(self._zone_points)}/4 at ({x},{y})")

        if len(self._zone_points) == 4:
            self._view.set_collecting(False)
            self._save_zone_to_settings()
            self._mask = None    # force rebuild on next reference save
            self._status = self.STATUS_NO_REF
            self._refresh_status()
            logger.info("Maintenance: zone defined. Click 'Save Reference' next.")

    # ── Reference + drift ──────────────────────────────────────────────────

    def _build_mask(self, shape: tuple[int, int]) -> np.ndarray | None:
        """Build binary mask (255 inside zone, 0 outside)."""
        if len(self._zone_points) != 4:
            return None
        h, w = shape
        mask = np.zeros((h, w), dtype=np.uint8)
        pts = np.array(self._zone_points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(mask, [pts], 255)
        return mask

    def _save_reference(self) -> None:
        if self._latest_frame is None:
            self._flash_status("No camera frame yet.")
            return
        if len(self._zone_points) != 4:
            self._flash_status("Define zone first (4 points).")
            return
        gray = cv2.cvtColor(self._latest_frame, cv2.COLOR_BGR2GRAY)
        self._mask = self._build_mask(gray.shape)
        # Store reference (full gray frame — mask applied during diff)
        self._reference_gray = gray.copy()
        REF_FRAME_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.save(REF_FRAME_PATH, self._reference_gray)
        self._status = self.STATUS_STABLE
        self._last_drift = 0.0
        self._refresh_status()
        logger.info(f"Maintenance: reference saved to {REF_FRAME_PATH}")

    def _check_drift(self, frame_bgr: np.ndarray) -> None:
        if self._reference_gray is None or len(self._zone_points) != 4:
            return

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if gray.shape != self._reference_gray.shape:
            logger.warning("Maintenance: frame shape mismatch — skipping drift check.")
            return

        if self._mask is None or self._mask.shape != gray.shape:
            self._mask = self._build_mask(gray.shape)
        if self._mask is None:
            return

        # Masked MSE
        diff = cv2.absdiff(self._reference_gray, gray)
        masked = cv2.bitwise_and(diff, diff, mask=self._mask)
        n_pixels = int(np.count_nonzero(self._mask))
        if n_pixels == 0:
            return
        mse = float(np.sum(masked.astype(np.float32) ** 2)) / n_pixels
        # scale to comparable 0-100 range
        drift_score = np.sqrt(mse)   # RMSE ~ 0-255
        self._last_drift = drift_score

        drifted = drift_score > self._threshold
        new_status = self.STATUS_DRIFTED if drifted else self.STATUS_STABLE
        if new_status != self._status:
            self._status = new_status
            self._refresh_status()
            # แจ้ง MainWindow เฉพาะตอน transition (ไม่ emit ทุก frame)
            self.drift_status_changed.emit(self._status, float(drift_score))
        else:
            # update drift numeric display even without status change
            self._drift_value.setText(f"Drift score: {drift_score:.1f} / threshold {self._threshold}")

    # ── Status refresh ─────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        s = self._status
        if s == self.STATUS_IDLE:
            self._instruct.setText(
                "1) กด 'Define Zone' แล้วคลิก 4 จุดบนภาพเพื่อล้อมบริเวณที่ต้อง monitor\n"
                "2) กด 'Save Reference' เพื่อเก็บภาพ reference\n"
                "3) ระบบจะแจ้งเมื่อภาพเคลื่อน/เปลี่ยนเกิน threshold"
            )
            self._status_text.setText("—")
            self._status_text.setStyleSheet("color: #52606d;")
            self._drift_value.setText("ยังไม่ได้กำหนด zone")
            self._view.set_drift_color(QColor("#52606d"))
        elif s == self.STATUS_COLLECT:
            self._instruct.setText(
                f"คลิกที่ภาพ — เลือก 4 จุดมุมของบริเวณที่ต้องการ monitor\n"
                f"เก็บจุดแล้ว: {len(self._zone_points)}/4"
            )
            self._status_text.setText("CLICK 4 POINTS")
            self._status_text.setStyleSheet("color: #1565c0;")
            self._drift_value.setText("")
            self._view.set_drift_color(QColor("#1565c0"))
        elif s == self.STATUS_NO_REF:
            self._instruct.setText(
                "Zone กำหนดแล้ว\n"
                "ต่อไป: กด 'Save Reference' เพื่อเก็บ snapshot ไว้เทียบ drift"
            )
            self._status_text.setText("ZONE SET — NO REF")
            self._status_text.setStyleSheet("color: #ef6c00;")
            self._drift_value.setText("รอการบันทึก reference")
            self._view.set_drift_color(QColor("#ef6c00"))
        elif s == self.STATUS_STABLE:
            self._instruct.setText(
                "กำลัง monitor zone — ภาพในบริเวณนี้ไม่ควรเปลี่ยนแปลงเกิน threshold\n"
                "ถ้าเห็น DRIFTED = กล้องอาจขยับ หรือ scene เปลี่ยน"
            )
            self._status_text.setText("ZONE STABLE")
            self._status_text.setStyleSheet("color: #2e7d32;")
            self._drift_value.setText(
                f"Drift score: {self._last_drift:.1f} / threshold {self._threshold}"
            )
            self._view.set_drift_color(QColor("#2e7d32"))
        elif s == self.STATUS_DRIFTED:
            self._instruct.setText(
                "ภาพในบริเวณที่ monitor เปลี่ยนแปลงเกิน threshold\n"
                "สาเหตุที่เป็นไปได้: กล้องขยับ / แสงเปลี่ยน / มีวัตถุบัง\n"
                "แก้: จัดกล้องให้ตรง แล้วกด 'Save Reference' ใหม่ หรือเพิ่ม threshold"
            )
            self._status_text.setText("ZONE DRIFTED")
            self._status_text.setStyleSheet("color: #c62828;")
            self._drift_value.setText(
                f"Drift score: {self._last_drift:.1f} / threshold {self._threshold}"
            )
            self._view.set_drift_color(QColor("#c62828"))

    def _flash_status(self, msg: str) -> None:
        self._instruct.setText(msg)

    @Slot(int)
    def _on_threshold_changed(self, value: int) -> None:
        self._threshold = value
        self._threshold_label.setText(str(value))
        self._settings.setValue("calibration/threshold", value)
        # re-evaluate immediately
        if self._latest_frame is not None:
            self._check_drift(self._latest_frame)
