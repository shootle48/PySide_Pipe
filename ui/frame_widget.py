"""
frame_widget.py
───────────────
Custom QWidget that renders the camera frame and detection bounding boxes
using QPainter — the PySide6 equivalent of canvas.js in the web version.

Two display modes (toggled by MainWindow):
  Live mode   — set_live_frame(frame_bgr)         called ~20 fps from frame_ready signal
  Result mode — set_result_frame(image_b64, dets)  called once per inspection
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

import cv2
import numpy as np

from PySide6.QtCore    import Qt
from PySide6.QtGui     import (
    QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QSizePolicy, QWidget


# ── Defect colour map (mirrors canvas.js DEFECT_COLORS) ───────────────────
_DEFECT_COLORS: dict[str, str] = {
    "defect":         "#ff1744",
    "crack":          "#ff6d00",
    "corrosion":      "#ffd600",
    "pipe_not_found": "#7c4dff",
}
_DEFAULT_COLOR = "#ff1744"


class FrameWidget(QWidget):
    """
    Displays one camera frame (live or inspection result) with optional
    bounding-box overlays drawn via QPainter.

    Usage
    ─────
    # Live view (called by frame_ready signal):
    widget.set_live_frame(frame_bgr)

    # Inspection result (called by result_ready signal):
    widget.set_result_frame(image_b64, detections)

    # Placeholder (initial state / after reset):
    widget.show_placeholder("กดปุ่ม Capture เพื่อถ่ายภาพ")
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap:     Optional[QPixmap] = None
        self._detections: list = []
        self._is_placeholder  = True
        self._placeholder_text = "กดปุ่ม Capture เพื่อถ่ายภาพและตรวจสอบ"

        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ── Public API ─────────────────────────────────────────────────────────

    def has_frame(self) -> bool:
        """Return True if a real frame (not placeholder) is currently displayed."""
        return not self._is_placeholder and self._pixmap is not None

    # ── Public setters ─────────────────────────────────────────────────────

    def set_live_frame(self, frame_bgr: np.ndarray) -> None:
        """
        Update display with a raw BGR frame from the camera.
        Called ~20 fps by the live emitter thread (via Signal).
        No detections are drawn in live mode.
        """
        h, w, ch = frame_bgr.shape
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        qimage = QImage(frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self._pixmap      = QPixmap.fromImage(qimage)
        self._detections  = []
        self._is_placeholder = False
        self.update()

    def set_result_frame(self, image_b64: str, detections: list) -> None:
        """
        Display the inspection result frame with bounding boxes.
        image_b64: base64-encoded JPEG (RGB, from pipeline._build_result).
        detections: list of {"label", "confidence", "bbox": {"x","y","w","h"}}.
        """
        try:
            jpg_bytes = base64.b64decode(image_b64)
            qimage    = QImage.fromData(jpg_bytes)
            if qimage.isNull():
                raise ValueError("QImage decode failed")
            self._pixmap = QPixmap.fromImage(qimage)
        except Exception as exc:
            logger.error(f"FrameWidget: cannot decode result image: {exc}")
            return
        self._detections  = detections
        self._is_placeholder = False
        self.update()

    def show_placeholder(self, text: str = "") -> None:
        """Revert to the placeholder state (no camera / reset)."""
        self._is_placeholder  = True
        self._placeholder_text = text or "กดปุ่ม Capture เพื่อถ่ายภาพและตรวจสอบ"
        self._pixmap           = None
        self._detections       = []
        self.update()

    # ── Qt painting ────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = self.rect()
        painter.fillRect(rect, QColor("#0d0f14"))

        if self._is_placeholder or self._pixmap is None:
            self._draw_placeholder(painter, rect)
            return

        # ── Scale pixmap to fit letterboxed (same as CSS object-fit: contain) ──
        scaled = self._pixmap.scaled(
            rect.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x_off = (rect.width()  - scaled.width())  // 2
        y_off = (rect.height() - scaled.height()) // 2
        painter.drawPixmap(x_off, y_off, scaled)

        # ── Bounding boxes ─────────────────────────────────────────────────
        if self._detections:
            scale_x = scaled.width()  / self._pixmap.width()
            scale_y = scaled.height() / self._pixmap.height()
            self._draw_detections(painter, self._detections, x_off, y_off, scale_x, scale_y)

    def _draw_placeholder(self, painter: QPainter, rect) -> None:
        """Draw centred placeholder text when no frame is available."""
        painter.setPen(QColor("#4a5070"))
        font = QFont("Segoe UI", 13)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, self._placeholder_text)

        # Camera icon (simplified SVG-style with QPainter)
        cx, cy = rect.center().x(), rect.center().y() - 40
        pen = QPen(QColor("#2a3050"), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(cx - 30, cy - 18, 60, 36, 4, 4)
        painter.drawEllipse(cx - 11, cy - 11, 22, 22)
        painter.drawEllipse(cx - 5, cy - 5, 10, 10)

    def _draw_detections(
        self,
        painter: QPainter,
        detections: list,
        x_off: int,
        y_off: int,
        scale_x: float,
        scale_y: float,
    ) -> None:
        """Draw bounding box + label chip for every detection."""
        box_font   = QFont("Consolas", 8, QFont.Bold)
        fm         = QFontMetrics(box_font)
        painter.setFont(box_font)

        for det in detections:
            bbox = det["bbox"]
            bx   = int(bbox["x"] * scale_x) + x_off
            by   = int(bbox["y"] * scale_y) + y_off
            bw   = int(bbox["w"] * scale_x)
            bh   = int(bbox["h"] * scale_y)

            color_hex = _DEFECT_COLORS.get(det["label"], _DEFAULT_COLOR)
            color     = QColor(color_hex)

            # Box
            pen = QPen(color, max(2, int(bw * 0.025)))
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(bx, by, bw, bh)

            # Label chip background + text
            label      = f"{det['label']}  {det['confidence']:.0%}"
            text_rect  = fm.boundingRect(label)
            chip_w     = text_rect.width() + 10
            chip_h     = 16
            chip_top   = max(0, by - chip_h)

            chip_color = QColor(color_hex)
            chip_color.setAlpha(220)
            painter.fillRect(bx, chip_top, chip_w, chip_h, chip_color)

            painter.setPen(QColor("white"))
            painter.drawText(bx + 5, chip_top + chip_h - 3, label)
