"""
main_window.py
──────────────
Full application window for the PySide6 Pipe Inspector.

Layout mirrors the web dashboard:
  ┌──────────────────────────────────────────────────────┐
  │  HEADER: [icon] PIPE INSPECTOR | Batch XXX | ● Idle  │
  ├──────────────────────────────────┬───────────────────┤
  │                                  │  BATCH COUNTERS   │
  │   [FrameWidget]                  │   TOTAL    NG     │
  │   (live stream / result canvas)  │   NG Rate         │
  │                                  │   [Reset Batch]   │
  │   [● LIVE]                       ├───────────────────┤
  │                                  │  LAST RESULT      │
  │                                  ├───────────────────┤
  │                                  │  RECENT INSPECT.  │
  │                                  │  (scrollable)     │
  ├──────────────────────────────────┤                   │
  │  [📷  Capture & Inspect]         │                   │
  └──────────────────────────────────┴───────────────────┘

Signal wiring:
  worker.frame_ready    → _on_frame_ready()   (live view update)
  worker.result_ready   → _on_result()        (inspection result)
  worker.status_changed → _on_status()        (status dot / text)
  worker.error_occurred → _on_worker_error()  (dialog)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from PySide6.QtCore    import QLocale, Qt, QSettings, QTimer, Slot
from PySide6.QtGui     import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.batch_state    import BatchStateManager
from core.database       import DatabaseManager
from core.pipeline       import CameraWorker
from core.size_classifier import SizeClassifier
from ui.frame_widget          import FrameWidget
from ui.db_viewer             import DbViewerDialog
from ui.camera_select_dialog  import CameraSelectDialog
from ui.maintenance_widget    import MaintenanceWidget
from ui.batch_setup_dialog    import request_batch_setup
from core.rs485_worker import RS485InputWorker, MockRS485DIO
# Note: `rs485_dio` (real hardware) imports lazily — see _init_rs485() below
# ไม่ import ตรงนี้เพราะต้องใช้ minimalmodbus/pyserial ที่ไม่มีบน Windows dev

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
CAMERA_INDEX     = 2
TRIGGER_MODE     = "manual"    # "manual" | "timer" | "gpio"
TIMER_INTERVAL   = 6.0
RESULT_VIEW_SECS = 4           # seconds to show result before returning to live

# RS485 I/O integration (trigger via external sensor/PLC)
#   "off"  — ไม่ใช้ RS485 เลย (dev บน Windows ที่ไม่มี hardware)
#   "mock" — ใช้ MockRS485DIO (test WFH — สุ่มยิง pulse ทุก 2s)
#   "real" — ใช้ RS485DIO จริง (Jetson + USB-to-RS485 dongle)
RS485_MODE       = "off"
RS485_WATCH_BITS = [0]         # inputs ที่ watch (I0 = trigger sensor default)

# ── Status colours (industrial HMI palette — high contrast for factory) ──
_STATUS_COLORS = {
    "idle":       "#52606d",   # medium gray
    "scanning":   "#0288d1",   # deep cyan/blue
    "processing": "#ef6c00",   # dark orange
    "connected":  "#2e7d32",   # dark green (WCAG AA on white)
    "error":      "#c62828",   # dark red
}


class MainWindow(QMainWindow):
    """
    Top-level application window.

    Creates and owns:
      - DatabaseManager  (SQLite)
      - BatchStateManager (in-memory + write-through)
      - CameraWorker     (QThread)

    All cross-thread communication happens through Qt Signals.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pipe Inspector")
        self.resize(1280, 760)
        self.setMinimumSize(900, 600)

        # ── Persisted settings ─────────────────────────────────────────────
        self._settings     = QSettings()   # uses app/org name from main.py
        self._camera_index = int(self._settings.value("camera/index", CAMERA_INDEX))

        # ── Backend singletons ─────────────────────────────────────────────
        self._db               = DatabaseManager()
        self._db.cleanup_old_data()   # cleanup ทุกครั้งที่เปิดโปรแกรม
        self._batch_state      = BatchStateManager(db=self._db)
        # SizeClassifier — โหลด thresholds จาก QSettings, fallback DEFAULT
        # (ใช้ใน PipeInspector ผ่าน CameraWorker)
        self._size_classifier  = SizeClassifier()
        self._worker           = self._build_worker(self._camera_index)

        # ── State ──────────────────────────────────────────────────────────
        self._in_result_view  = False
        self._upload_mode     = False   # True = Upload Image mode, False = Camera mode
        self._file_inspecting = False   # guard: ป้องกัน inspect_file() วิ่ง parallel
        self._result_timer    = QTimer(self)
        self._result_timer.setSingleShot(True)
        self._result_timer.timeout.connect(self._return_to_live)

        # ── Build UI ───────────────────────────────────────────────────────
        self._build_ui()
        self._apply_stylesheet()

        # ── Connect signals ────────────────────────────────────────────────
        self._connect_worker_signals()

        # ── Load existing history + sync counters ──────────────────────────
        state = self._batch_state.get_state()
        self._update_counters(state)
        self._batch_id_label.setText(state["id"])
        self._load_history(state["id"])

        # ── Start worker ───────────────────────────────────────────────────
        self._worker.start()
        logger.info("CameraWorker started.")

        # ── Start RS485 input worker (optional) ────────────────────────────
        self._io_worker: Optional[RS485InputWorker] = None
        self._io_source = None   # keep reference so GC doesn't kill mock
        self._init_rs485()

    def _init_rs485(self) -> None:
        """
        Set up RS485InputWorker based on RS485_MODE config.
        Off → noop. Mock → fake pulse every 2s. Real → load hardware driver.
        """
        if RS485_MODE == "off":
            logger.info("RS485: disabled (RS485_MODE='off').")
            return

        if RS485_MODE == "mock":
            logger.info("RS485: starting in MOCK mode — fake pulse every 2s.")
            self._io_source = MockRS485DIO(
                mode="auto_pulse", pulse_bit=0, pulse_interval_s=2.0,
            )

        elif RS485_MODE == "real":
            try:
                from rs485_dio import RS485DIO   # lazy — needs minimalmodbus
            except ImportError as exc:
                logger.error(
                    f"RS485: cannot import rs485_dio ({exc}). "
                    f"Install `pip install minimalmodbus pyserial` or set RS485_MODE='off'/'mock'."
                )
                return
            try:
                self._io_source = RS485DIO()
            except Exception as exc:
                logger.error(f"RS485: init failed ({exc}). Running without RS485 input.")
                return
            logger.info("RS485: real hardware mode OK.")

        else:
            logger.warning(f"RS485: unknown mode '{RS485_MODE}' — disabled.")
            return

        self._io_worker = RS485InputWorker(
            io=self._io_source, watch_bits=RS485_WATCH_BITS,
        )
        self._io_worker.pulse_detected.connect(self._on_rs485_pulse)
        self._io_worker.io_health_changed.connect(self._on_rs485_health)
        self._io_worker.start()

    @Slot(int)
    def _on_rs485_pulse(self, bit: int) -> None:
        """RS485 input rising edge → trigger inspection (เหมือนกด capture)"""
        logger.info(f"RS485 pulse on bit {bit} → triggering inspection")
        self._worker.trigger()

    @Slot(bool)
    def _on_rs485_health(self, online: bool) -> None:
        if not online:
            logger.warning("RS485: I/O offline")
        else:
            logger.info("RS485: I/O online")

    # ══════════════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self._build_tabs(), stretch=1)

        self.setCentralWidget(root)

    def _build_tabs(self) -> QTabWidget:
        """Wrap main area in tabs: Inspection (default) | Maintenance."""
        self._tabs = QTabWidget()
        self._tabs.setObjectName("mainTabs")

        # Tab 1: existing inspection UI
        self._tabs.addTab(self._build_main_area(), "Inspection")

        # Tab 2: maintenance / calibration
        self._maintenance = MaintenanceWidget()
        self._tabs.addTab(self._maintenance, "Maintenance")

        return self._tabs

    # ── Header ─────────────────────────────────────────────────────────────

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(72)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(18)

        # Left — App title
        title = QLabel("PIPE INSPECTOR")
        title.setObjectName("appTitle")
        layout.addWidget(title)

        layout.addStretch(1)

        # Centre — Batch ID
        layout.addWidget(QLabel("Batch"))
        self._batch_id_label = QLabel("—")
        self._batch_id_label.setObjectName("batchId")
        layout.addWidget(self._batch_id_label)

        layout.addStretch(1)

        # Right — Status dot + text + DB button
        self._status_dot = QLabel("●")
        self._status_dot.setObjectName("statusDot")
        self._status_dot.setFixedWidth(18)
        layout.addWidget(self._status_dot)

        self._status_text = QLabel("Connecting…")
        self._status_text.setObjectName("statusText")
        layout.addWidget(self._status_text)

        # Inference time indicator (updates per inspection)
        self._inference_label = QLabel("— ms")
        self._inference_label.setObjectName("inferenceLabel")
        self._inference_label.setToolTip("เวลาประมวลผล inspection ล่าสุด")
        layout.addWidget(self._inference_label)

        cam_btn = QPushButton("Camera")
        cam_btn.setObjectName("dbBtn")   # ใช้ style เดียวกับ DB button
        cam_btn.setFixedHeight(44)
        cam_btn.setMinimumWidth(110)
        cam_btn.setToolTip("เลือก/เปลี่ยนกล้องที่ใช้")
        cam_btn.clicked.connect(self._open_camera_select)
        layout.addWidget(cam_btn)

        db_btn = QPushButton("Database")
        db_btn.setObjectName("dbBtn")
        db_btn.setFixedHeight(44)
        db_btn.setMinimumWidth(110)
        db_btn.clicked.connect(self._open_db_viewer)
        layout.addWidget(db_btn)

        return header

    # ── Main area (splitter) ───────────────────────────────────────────────

    def _build_main_area(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_frame_panel())
        splitter.addWidget(self._build_info_panel())
        splitter.setSizes([900, 360])

        return splitter

    # ── Left panel (camera view + capture button) ──────────────────────────

    def _build_frame_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("framePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Frame view area (relative container for LIVE badge overlay + NG flash)
        self._view_container = QWidget()
        self._view_container.setObjectName("viewContainer")
        view_layout = QVBoxLayout(self._view_container)
        view_layout.setContentsMargins(0, 0, 0, 0)

        self._frame_widget = FrameWidget()
        view_layout.addWidget(self._frame_widget)

        # LIVE badge — absolute positioned over the frame widget
        self._live_badge = QLabel("● LIVE", self._view_container)
        self._live_badge.setObjectName("liveBadge")
        self._live_badge.move(12, 12)
        self._live_badge.setVisible(False)   # shown after camera opens

        layout.addWidget(self._view_container, stretch=1)

        # Mode toggle + action button bar
        capture_bar = QFrame()
        capture_bar.setObjectName("captureBar")
        capture_bar.setFixedHeight(132)
        bar_layout = QVBoxLayout(capture_bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(10)

        # ── Mode toggle row ────────────────────────────────────────────────
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)

        self._camera_mode_btn = QPushButton("Camera")
        self._camera_mode_btn.setObjectName("modeActive")
        self._camera_mode_btn.setFixedHeight(44)
        self._camera_mode_btn.clicked.connect(self._set_camera_mode)

        self._upload_mode_btn = QPushButton("Upload Image")
        self._upload_mode_btn.setObjectName("modeInactive")
        self._upload_mode_btn.setFixedHeight(44)
        self._upload_mode_btn.clicked.connect(self._set_upload_mode)

        toggle_row.addWidget(self._camera_mode_btn)
        toggle_row.addWidget(self._upload_mode_btn)
        toggle_row.addStretch()
        bar_layout.addLayout(toggle_row)

        # ── Action button (swaps between Camera / Upload) ──────────────────
        self._capture_btn = QPushButton("CAPTURE & INSPECT")
        self._capture_btn.setObjectName("captureBtn")
        self._capture_btn.setFixedHeight(64)
        self._capture_btn.clicked.connect(self._trigger_capture)
        bar_layout.addWidget(self._capture_btn)

        self._upload_btn = QPushButton("UPLOAD & INSPECT")
        self._upload_btn.setObjectName("uploadBtn")
        self._upload_btn.setFixedHeight(64)
        self._upload_btn.clicked.connect(self._upload_and_inspect)
        self._upload_btn.setVisible(False)
        bar_layout.addWidget(self._upload_btn)

        layout.addWidget(capture_bar)
        return panel

    # ── Right panel (counters + last result + history) ─────────────────────

    def _build_info_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("infoPanel")
        panel.setFixedWidth(420)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 10, 10)
        layout.setSpacing(10)

        # ── Batch counters card ────────────────────────────────────────────
        counters_card = self._make_card("BATCH COUNTERS")
        c_layout = counters_card.layout()

        nums_row = QHBoxLayout()
        nums_row.setSpacing(10)

        self._counter_total = self._make_big_counter("TOTAL", "#1a1d23")
        self._counter_ng    = self._make_big_counter("NG",    "#c62828")
        nums_row.addWidget(self._counter_total[0])
        nums_row.addWidget(self._counter_ng[0])
        c_layout.addLayout(nums_row)

        # OEE Quality Rate row — % ของ Good ต่อ Total (ตามที่ factory ใช้)
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Quality Rate"))
        self._ng_rate_label = QLabel("—")
        self._ng_rate_label.setObjectName("ngRate")
        self._ng_rate_label.setAlignment(Qt.AlignRight)
        rate_row.addWidget(self._ng_rate_label, stretch=1)
        c_layout.addLayout(rate_row)

        # Target vs Actual row
        exp_row = QHBoxLayout()
        exp_row.addWidget(QLabel("Target"))
        self._expected_label = QLabel("—")
        self._expected_label.setAlignment(Qt.AlignRight)
        self._expected_label.setObjectName("expectedLabel")
        exp_row.addWidget(self._expected_label, stretch=1)
        c_layout.addLayout(exp_row)

        # Size row — ขนาดที่ batch นี้ล็อคไว้ (S/M/L) — read-only display
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size"))
        self._size_label = QLabel("—")
        self._size_label.setAlignment(Qt.AlignRight)
        self._size_label.setObjectName("expectedLabel")
        size_row.addWidget(self._size_label, stretch=1)
        c_layout.addLayout(size_row)

        # Progress bar — แสดงความคืบหน้าเทียบกับ expected_total
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("batchProgress")
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m  (%p%)")
        self._progress_bar.setFixedHeight(28)
        # Force English locale เพื่อให้ใช้เลขอารบิก (0-9) แทนเลขไทย (๐-๙)
        self._progress_bar.setLocale(QLocale(QLocale.English, QLocale.UnitedStates))
        self._progress_bar.setVisible(False)   # ซ่อนจนกว่าจะ set expected
        c_layout.addWidget(self._progress_bar)

        self._missing_label = QLabel("")
        self._missing_label.setAlignment(Qt.AlignCenter)
        self._missing_label.setObjectName("missingLabel")
        c_layout.addWidget(self._missing_label)

        # Set / Edit target button
        self._expected_btn = QPushButton("Set Target")
        self._expected_btn.setObjectName("secondaryBtn")
        self._expected_btn.clicked.connect(self._set_expected)
        c_layout.addWidget(self._expected_btn)

        # Reset button
        self._reset_btn = QPushButton("Reset Batch")
        self._reset_btn.setObjectName("resetBtn")
        self._reset_btn.clicked.connect(self._reset_batch)
        c_layout.addWidget(self._reset_btn)

        layout.addWidget(counters_card)

        # ── Last result card ───────────────────────────────────────────────
        last_card = self._make_card("LAST RESULT")
        self._last_result_layout = last_card.layout()
        self._last_result_placeholder = QLabel("No result yet.")
        self._last_result_placeholder.setObjectName("dimText")
        self._last_result_layout.addWidget(self._last_result_placeholder)
        layout.addWidget(last_card)

        # ── History feed card ──────────────────────────────────────────────
        history_card = self._make_card("RECENT INSPECTIONS")
        h_layout = history_card.layout()
        h_layout.setSpacing(4)

        self._history_list = QListWidget()
        self._history_list.setObjectName("historyList")
        self._history_list.setSpacing(1)
        self._history_list.setSelectionMode(QListWidget.NoSelection)
        h_layout.addWidget(self._history_list, stretch=1)

        history_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(history_card, stretch=1)

        return panel

    # ── Widget helpers ─────────────────────────────────────────────────────

    def _make_card(self, title: str) -> QFrame:
        """Create a titled dark card container (matches .card in CSS)."""
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)
        return card

    def _make_big_counter(self, label: str, color: str) -> tuple[QFrame, QLabel]:
        """Create a big numeric counter box (TOTAL / NG style)."""
        box = QFrame()
        box.setObjectName("counterBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setAlignment(Qt.AlignCenter)

        lbl = QLabel(label)
        lbl.setObjectName("counterLabel")
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)

        value_lbl = QLabel("0")
        value_lbl.setObjectName("counterValue")
        value_lbl.setAlignment(Qt.AlignCenter)
        value_lbl.setStyleSheet(f"color: {color};")
        layout.addWidget(value_lbl)

        return box, value_lbl

    # ══════════════════════════════════════════════════════════════════════
    # Slots — called from CameraWorker signals (may arrive from another thread,
    # but Qt routes them to the main thread automatically via Signal)
    # ══════════════════════════════════════════════════════════════════════

    @Slot(object)
    def _on_frame_ready(self, frame_bgr) -> None:
        """Update live view — only when not displaying an inspection result."""
        if not self._in_result_view:
            self._frame_widget.set_live_frame(frame_bgr)
            self._live_badge.setVisible(True)
        # Always forward to maintenance tab (drift monitor runs in background)
        self._maintenance.on_frame(frame_bgr)

    @Slot(dict)
    def _on_result(self, result: dict) -> None:
        """
        Inspection result arrived.
          1. Switch frame widget to result view (bbox overlay).
          2. Update counters, last result card, history list.
          3. Flash the verdict colour on the capture button border briefly.
          4. Schedule return to live view after RESULT_VIEW_SECS.
        """
        self._in_result_view = True
        self._live_badge.setVisible(False)
        self._frame_widget.set_result_frame(result["image_b64"], result["detections"])

        # Update inference time indicator (สีแดงถ้าช้าผิดปกติ >500ms)
        ms = result.get("inference_ms")
        if ms is not None:
            color = "#c62828" if ms > 500 else ("#ef6c00" if ms > 250 else "#52606d")
            self._inference_label.setText(f"{ms:.0f} ms")
            self._inference_label.setStyleSheet(f"color: {color};")

        # Update batch counters
        self._update_counters(result["batch"])

        # Update last result card
        self._update_last_result(result)

        # Prepend to history
        self._prepend_history_row(result)

        # Flash capture button
        verdict = result["verdict"]
        flash_color = "#2e7d32" if verdict == "OK" else "#c62828"
        self._capture_btn.setStyleSheet(
            f"#captureBtn {{ border: 3px solid {flash_color}; }}"
        )
        QTimer.singleShot(600, lambda: self._capture_btn.setStyleSheet(""))

        # Visual NG alert — flash red border around frame for 500ms
        if verdict == "NG":
            self._flash_ng_alert()

        # Re-enable active button
        self._capture_btn.setEnabled(True)
        self._capture_btn.setText("CAPTURE & INSPECT")
        self._upload_btn.setEnabled(True)
        self._upload_btn.setText("UPLOAD & INSPECT")

        # Return to live view after delay
        self._result_timer.start(RESULT_VIEW_SECS * 1000)

    @Slot(str)
    def _on_status(self, state: str) -> None:
        """Update the status dot and text in the header."""
        labels = {
            "idle":       "Idle",
            "scanning":   "Scanning…",
            "processing": "Processing…",
        }
        color = _STATUS_COLORS.get(state, "#52606d")
        self._status_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        self._status_text.setText(labels.get(state, state.title()))

        # Animate capture button during processing
        if state == "processing":
            self._capture_btn.setEnabled(False)
            self._capture_btn.setText("PROCESSING…")
        elif state == "scanning":
            self._capture_btn.setEnabled(False)
            self._capture_btn.setText("SCANNING…")

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        """Show error dialog. In Upload mode, camera errors are suppressed."""
        if self._upload_mode and "camera" in message.lower():
            # กล้องเปิดไม่ได้ — ปกติใน Upload mode ไม่ต้องแจ้งเตือน
            logger.warning(f"Camera unavailable (Upload mode): {message}")
            self._status_dot.setStyleSheet(f"color: {_STATUS_COLORS['idle']}; font-size: 14px;")
            self._status_text.setText("Upload Mode")
            return
        QMessageBox.critical(self, "Error", message)
        self._status_dot.setStyleSheet(f"color: {_STATUS_COLORS['error']}; font-size: 18px;")
        self._status_text.setText("Error")
        self._capture_btn.setEnabled(False)
        self._upload_btn.setEnabled(True)
        self._upload_btn.setText("UPLOAD & INSPECT")

    @Slot(bool)
    def _on_camera_health(self, online: bool) -> None:
        """
        Camera online/offline indicator.
        - online=False: แสดง "Camera offline" + disable capture button
        - online=True : กลับมา "Camera online" + enable capture
        (ไม่เด้ง dialog เพราะ offline ไม่ใช่ fatal — recover เองได้)
        """
        if self._upload_mode:
            return   # Upload mode ไม่สนกล้อง

        if online:
            self._status_dot.setStyleSheet(
                f"color: {_STATUS_COLORS['connected']}; font-size: 18px;"
            )
            self._status_text.setText("Camera online")
            self._capture_btn.setEnabled(True)
            logger.info("UI: camera back online")
        else:
            self._status_dot.setStyleSheet(
                f"color: {_STATUS_COLORS['error']}; font-size: 18px;"
            )
            self._status_text.setText("Camera offline")
            self._capture_btn.setEnabled(False)
            self._live_badge.setVisible(False)
            logger.warning("UI: camera offline")

    # ══════════════════════════════════════════════════════════════════════
    # UI update helpers
    # ══════════════════════════════════════════════════════════════════════

    def _return_to_live(self) -> None:
        """Called by _result_timer after RESULT_VIEW_SECS seconds."""
        self._in_result_view = False
        self._live_badge.setVisible(True)

    def _flash_ng_alert(self) -> None:
        """
        Visual NG alert: เปลี่ยนกรอบ view_container เป็นแดงหนา ~500ms
        แล้ว revert กลับ default (ไม่มีเสียง, ไม่บังภาพ)
        """
        self._view_container.setStyleSheet(
            "#viewContainer {"
            "  background: #1a1d23;"
            "  border: 5px solid #c62828;"
            "  border-radius: 6px;"
            "}"
        )
        # Revert หลัง 500ms — reset เป็น empty string เพื่อให้ใช้ global stylesheet default
        QTimer.singleShot(
            500,
            lambda: self._view_container.setStyleSheet("")
        )

    def _update_counters(self, batch: dict) -> None:
        """Refresh TOTAL / NG / Quality Rate / Target / Size from a batch snapshot dict."""
        total    = batch.get("total", 0)
        ng       = batch.get("ng", 0)
        expected = batch.get("expected_total", 0)
        exp_size = batch.get("expected_size", "") or ""

        self._counter_total[1].setText(str(total))
        self._counter_ng[1].setText(str(ng))

        # Size display — "—" ถ้ายังไม่ล็อค (legacy batch ก่อน feature นี้)
        self._size_label.setText(exp_size if exp_size else "—")

        # OEE Quality Rate = Good / Total × 100   (ตามนิยาม OEE มาตรฐาน)
        # ถ้า NG = 0 → 100%   ถ้า NG = total → 0%
        if total > 0:
            quality_rate = (total - ng) / total * 100
            self._ng_rate_label.setText(f"{quality_rate:.1f} %")
        else:
            self._ng_rate_label.setText("—")

        # Expected vs actual
        if expected > 0:
            self._expected_label.setText(str(expected))
            diff = expected - total

            # Progress bar — show & colour by state
            self._progress_bar.setVisible(True)
            # Cap maximum ไว้ที่ expected แต่ถ้า total เกิน → fill เต็ม + ใช้สีแดง
            self._progress_bar.setMaximum(expected)
            self._progress_bar.setValue(min(total, expected))

            if diff > 0:
                bar_color = "#1565c0"   # ฟ้า — ยังไม่ครบ
                self._missing_label.setText(f"ขาดอีก {diff} ชิ้น")
                self._missing_label.setStyleSheet("color:#ef6c00; font-weight:bold;")
            elif diff < 0:
                bar_color = "#c62828"   # แดง — เกินเป้า
                self._missing_label.setText(f"เกิน {abs(diff)} ชิ้น")
                self._missing_label.setStyleSheet("color:#c62828; font-weight:bold;")
            else:
                bar_color = "#2e7d32"   # เขียว — ครบพอดี
                self._missing_label.setText("ครบตามเป้า")
                self._missing_label.setStyleSheet("color:#2e7d32; font-weight:bold;")

            self._progress_bar.setStyleSheet(
                "QProgressBar#batchProgress {"
                "  background:#f1f3f6; border:1px solid #cbd1d9;"
                "  border-radius:6px; color:#1a1d23;"
                "  font-family:'Segoe UI',sans-serif; font-size:13px;"
                "  font-weight:bold; text-align:center;"
                "}"
                f"QProgressBar#batchProgress::chunk {{ background:{bar_color}; border-radius:5px; }}"
            )
        else:
            self._expected_label.setText("—")
            self._missing_label.setText("")
            self._progress_bar.setVisible(False)

        self._batch_id_label.setText(batch.get("id", "—"))

    def _update_last_result(self, result: dict) -> None:
        """Replace last-result card content with the new result."""
        # Clear old widgets
        while self._last_result_layout.count() > 1:   # keep title at index 0
            item = self._last_result_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        verdict   = result["verdict"]
        piece_id  = result.get("piece_id", "—")
        timestamp = result.get("timestamp", "")
        confs     = result.get("detections", [])

        # Verdict badge
        verdict_lbl = QLabel(verdict)
        verdict_lbl.setObjectName(f"verdictBadge_{verdict}")
        verdict_lbl.setAlignment(Qt.AlignCenter)
        self._last_result_layout.addWidget(verdict_lbl)

        # Piece ID + time
        meta_row = QHBoxLayout()
        piece_lbl = QLabel(piece_id)
        piece_lbl.setObjectName("dimText")
        meta_row.addWidget(piece_lbl)
        meta_row.addStretch()
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.astimezone().strftime("%H:%M:%S")
            except Exception:
                time_str = timestamp[-8:]
            time_lbl = QLabel(time_str)
            time_lbl.setObjectName("dimText")
            meta_row.addWidget(time_lbl)
        self._last_result_layout.addLayout(meta_row)

        # Detection rows
        if confs:
            for det in confs[:3]:
                row = QHBoxLayout()
                lbl = QLabel(det["label"].replace("_", " ").title())
                conf = QLabel(f"{det['confidence']:.0%}")
                conf.setObjectName("ngText")
                row.addWidget(lbl)
                row.addStretch()
                row.addWidget(conf)
                self._last_result_layout.addLayout(row)
        else:
            ok_lbl = QLabel("No defects detected")
            ok_lbl.setObjectName("okText")
            self._last_result_layout.addWidget(ok_lbl)

    def _prepend_history_row(self, result: dict) -> None:
        """Add one row at the top of the history list."""
        verdict  = result["verdict"]
        piece_id = result.get("piece_id", "—")
        confs    = result.get("detections", [])
        timestamp = result.get("timestamp", "")
        det_size = result.get("detected_size", "") or ""

        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            time_str = dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            time_str = "—"

        detail = confs[0]["label"].replace("_", " ") if confs else "No defect"
        tag    = "[OK]" if verdict == "OK" else "[NG]"
        # Size code 1 ตัว (S/M/L/?/-) ใส่ระหว่าง piece_id กับ detail
        size_code = (det_size[0] if det_size and det_size != "unknown" else "?") if det_size else "-"
        text   = f"  {tag}  {piece_id:<20}  {size_code}  {detail:<18}  {time_str}"

        item = QListWidgetItem(text)
        item.setFont(QFont("Consolas", 12, QFont.Bold))
        if verdict == "NG":
            item.setForeground(QColor("#c62828"))
            item.setBackground(QColor("#ffebee"))
        else:
            item.setForeground(QColor("#2e7d32"))
            item.setBackground(QColor("#e8f5e9"))

        self._history_list.insertItem(0, item)

        # Keep list from growing without bound
        while self._history_list.count() > 200:
            self._history_list.takeItem(self._history_list.count() - 1)

    def _load_history(self, batch_id: str) -> None:
        """Populate the history list from the database on startup."""
        self._history_list.clear()
        rows = self._db.get_recent_inspections(batch_id, limit=50)
        for row in reversed(rows):   # DB ส่งมา DESC → reverse ก่อน insertItem(0) จะได้ลำดับถูก
            self._prepend_history_row({
                "verdict":       row["verdict"],
                "piece_id":      row["piece_id"],
                "detections":    row["detections"],
                "timestamp":     row["timestamp"],
                "detected_size": row.get("detected_size", "") if isinstance(row, dict) else "",
            })
        if not rows:
            placeholder = QListWidgetItem("  No inspections in this batch yet.")
            placeholder.setForeground(QColor("#7b8794"))
            self._history_list.addItem(placeholder)

    # ══════════════════════════════════════════════════════════════════════
    # Button handlers
    # ══════════════════════════════════════════════════════════════════════

    def _open_db_viewer(self) -> None:
        """Open the database viewer dialog."""
        dialog = DbViewerDialog(db=self._db, parent=self)
        dialog.exec()
        # Sync in-memory batch state กลับจาก DB หลัง DB Viewer อาจลบ record ไป
        state = self._batch_state.sync_from_db()
        self._update_counters(state)

    # ── Camera management ─────────────────────────────────────────────────

    def _build_worker(self, camera_index: int) -> CameraWorker:
        """Factory: สร้าง CameraWorker ใหม่ด้วย index ที่กำหนด"""
        return CameraWorker(
            batch_state     = self._batch_state,
            db              = self._db,
            camera_index    = camera_index,
            trigger_mode    = TRIGGER_MODE,
            timer_interval  = TIMER_INTERVAL,
            size_classifier = self._size_classifier,
        )

    def _connect_worker_signals(self) -> None:
        """Connect signals ของ worker ปัจจุบันเข้า slots ของ MainWindow"""
        self._worker.frame_ready.connect(self._on_frame_ready)
        self._worker.result_ready.connect(self._on_result)
        self._worker.status_changed.connect(self._on_status)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.camera_health_changed.connect(self._on_camera_health)

    def _open_camera_select(self) -> None:
        """เปิด dialog เลือกกล้อง → switch ถ้าเลือก index ใหม่"""
        dialog = CameraSelectDialog(current_index=self._camera_index, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        new_idx = dialog.selected_index()
        if new_idx is None or new_idx == self._camera_index:
            logger.info("Camera select: no change")
            return

        self._switch_camera(new_idx)

    def _switch_camera(self, new_idx: int) -> None:
        """
        Stop worker ปัจจุบัน → สร้าง worker ใหม่ด้วย index ใหม่ → start
        Persist index ใหม่ลง QSettings ให้ startup ครั้งหน้าใช้อัตโนมัติ
        """
        old_idx = self._camera_index
        logger.info(f"Switching camera: {old_idx} → {new_idx}")

        # แจ้ง user ว่ากำลังเปลี่ยน
        self._status_text.setText(f"Switching to camera {new_idx}…")
        self._frame_widget.show_placeholder(f"กำลังเปลี่ยนกล้อง → Camera {new_idx}")
        self._live_badge.setVisible(False)
        self._capture_btn.setEnabled(False)

        # Stop worker เก่า
        self._worker.stop()
        self._worker.wait(3000)

        # Build + start worker ใหม่
        self._camera_index = new_idx
        self._worker       = self._build_worker(new_idx)
        self._connect_worker_signals()
        self._worker.start()

        # Persist ลง QSettings
        self._settings.setValue("camera/index", new_idx)
        self._settings.sync()

        logger.info(f"Camera switched to {new_idx} (persisted)")

    def _set_camera_mode(self) -> None:
        """Switch to Camera mode."""
        self._upload_mode = False
        self._camera_mode_btn.setObjectName("modeActive")
        self._upload_mode_btn.setObjectName("modeInactive")
        self._camera_mode_btn.setStyle(self._camera_mode_btn.style())
        self._upload_mode_btn.setStyle(self._upload_mode_btn.style())
        self._capture_btn.setVisible(True)
        self._upload_btn.setVisible(False)
        self._live_badge.setVisible(self._frame_widget.has_frame())
        logger.info("Mode: Camera")

    def _set_upload_mode(self) -> None:
        """Switch to Upload Image mode."""
        self._upload_mode = True
        self._camera_mode_btn.setObjectName("modeInactive")
        self._upload_mode_btn.setObjectName("modeActive")
        self._camera_mode_btn.setStyle(self._camera_mode_btn.style())
        self._upload_mode_btn.setStyle(self._upload_mode_btn.style())
        self._capture_btn.setVisible(False)
        self._upload_btn.setVisible(True)
        self._upload_btn.setEnabled(True)
        self._upload_btn.setText("UPLOAD & INSPECT")
        self._live_badge.setVisible(False)
        self._frame_widget.show_placeholder("Upload mode — กดปุ่มด้านล่างเพื่อเลือกรูปภาพ")
        logger.info("Mode: Upload Image")

    def _trigger_capture(self) -> None:
        """Fire a manual inspection trigger."""
        self._capture_btn.setEnabled(False)
        self._capture_btn.setText("SCANNING…")
        self._worker.trigger()

    def _upload_and_inspect(self) -> None:
        """Open file dialog → inspect the selected image file."""
        if self._file_inspecting:
            return   # ป้องกัน double-click ขณะกำลัง process อยู่

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif)",
        )
        if not path:
            return

        self._file_inspecting = True
        self._upload_btn.setEnabled(False)
        self._upload_btn.setText("PROCESSING…")

        def _run():
            try:
                self._worker.inspect_file(path)
            finally:
                self._file_inspecting = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _reset_batch(self) -> None:
        """Reset batch counters and return to live view.

        ใช้ BatchSetupDialog ใหม่ — เลือก size (S/M/L) + Target ในหน้าเดียว
        Size ถูกล็อคต่อ batch (เปลี่ยนได้แค่ผ่าน Reset ตัวนี้เท่านั้น).
        """
        current = self._batch_state.get_state()
        result = request_batch_setup(
            parent         = self,
            default_size   = current.get("expected_size") or "M",
            default_target = current.get("expected_total", 0),
        )
        if result is None:
            return   # user cancelled
        size, target = result

        new_state = self._batch_state.reset(
            expected_total = target,
            expected_size  = size,
        )
        self._update_counters(new_state)
        self._history_list.clear()
        self._frame_widget.show_placeholder(
            f"Batch ใหม่ (size={size}, target={target}) — กดปุ่ม Capture เพื่อเริ่ม"
        )

    def _set_expected(self) -> None:
        """เปลี่ยน target ของ batch ปัจจุบันโดยไม่ reset."""
        current = self._batch_state.get_state().get("expected_total", 0)

        # ใช้ custom dialog แทน QInputDialog.getInt เพื่อ set locale ป้องกันเลขไทย
        dlg = QDialog(self)
        dlg.setWindowTitle("Set Target")
        dlg.setModal(True)
        dlg.setMinimumWidth(320)
        _layout = QVBoxLayout(dlg)
        _layout.setContentsMargins(20, 20, 20, 20)
        _layout.setSpacing(12)
        _lbl = QLabel("จำนวนชิ้นที่ตั้งเป้าใน batch นี้\n(0 = ไม่ระบุ)")
        _layout.addWidget(_lbl)
        _spin = QSpinBox()
        _spin.setRange(0, 1_000_000)
        _spin.setValue(current)
        _spin.setFixedHeight(44)
        _spin.setAlignment(Qt.AlignCenter)
        # Force Arabic numerals — ป้องกันเลขไทย (๐-๙) บน locale ภาษาไทย
        _spin.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        _layout.addWidget(_spin)
        _btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _btns.accepted.connect(dlg.accept)
        _btns.rejected.connect(dlg.reject)
        _layout.addWidget(_btns)

        if dlg.exec() != QDialog.Accepted:
            return
        expected = _spin.value()
        new_state = self._batch_state.set_expected_total(expected)
        self._update_counters(new_state)

        # Clear last result card
        while self._last_result_layout.count() > 1:
            item = self._last_result_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        # Return to live view
        self._result_timer.stop()
        self._in_result_view = False
        self._live_badge.setVisible(True)

        logger.info(f"Batch reset → {new_state['id']}")

    # ══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        """Clean shutdown: stop worker thread + close DB connection."""
        logger.info("MainWindow: closing — stopping worker...")
        self._result_timer.stop()
        self._worker.stop()
        self._worker.wait(3000)   # wait up to 3 s for clean exit
        if self._io_worker is not None:
            self._io_worker.stop()
            self._io_worker.wait(2000)
        if self._io_source is not None and hasattr(self._io_source, "stop"):
            self._io_source.stop()   # MockRS485DIO has its own pulse thread
        self._db.close()
        logger.info("MainWindow: shutdown complete.")
        event.accept()

    # ══════════════════════════════════════════════════════════════════════
    # Stylesheet
    # ══════════════════════════════════════════════════════════════════════

    def _apply_stylesheet(self) -> None:
        """
        Light industrial HMI theme — สำหรับ factory ใช้งานบน 14" touchscreen
        ภายใต้แสงแรง: พื้นขาว, ตัวอักษรใหญ่, touch target >= 44px,
        สี contrast สูง (WCAG AA) สำหรับ verdict OK/NG
        """
        self.setStyleSheet("""
            /* ── Global ───────────────────────────────────────────────── */
            QMainWindow, QWidget {
                background: #eef0f3;
                color: #1a1d23;
                font-family: "Segoe UI", "Inter", system-ui, sans-serif;
                font-size: 14px;
            }
            QToolTip {
                background: #1a1d23;
                color: #ffffff;
                border: 1px solid #52606d;
                padding: 6px 10px;
                font-size: 13px;
            }

            /* ── Tab Bar ──────────────────────────────────────────────── */
            QTabWidget#mainTabs::pane {
                border: none;
                background: #eef0f3;
            }
            QTabBar::tab {
                background: #dde1e7;
                color: #52606d;
                padding: 12px 26px;
                border: 1px solid #cbd1d9;
                border-bottom: none;
                min-width: 160px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1565c0;
                border-bottom: 3px solid #1565c0;
            }
            QTabBar::tab:hover:!selected {
                background: #e6eaf0;
                color: #1a1d23;
            }

            /* ── Header ───────────────────────────────────────────────── */
            #header {
                background: #ffffff;
                border-bottom: 2px solid #cbd1d9;
            }
            #appTitle {
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                color: #1a1d23;
            }
            #batchId {
                font-family: "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                background: #1565c0;
                border: 1px solid #0d47a1;
                padding: 5px 12px;
                border-radius: 6px;
            }
            #statusText {
                font-size: 14px;
                font-weight: 600;
                color: #1a1d23;
            }
            #statusDot {
                font-size: 18px;
            }
            #inferenceLabel {
                font-family: "Consolas", monospace;
                font-size: 13px;
                font-weight: bold;
                color: #52606d;
                background: #f1f3f6;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 70px;
            }
            #dbBtn {
                background: #ffffff;
                color: #1a1d23;
                border: 2px solid #a8b0ba;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 14px;
            }
            #dbBtn:hover {
                background: #e3f2fd;
                border-color: #1565c0;
                color: #0d47a1;
            }
            #dbBtn:pressed {
                background: #bbdefb;
            }

            /* ── Frame panel ──────────────────────────────────────────── */
            #framePanel {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 8px;
                margin: 10px 6px 10px 10px;
            }
            #viewContainer {
                background: #1a1d23;
                border-radius: 6px;
            }

            /* ── LIVE badge ───────────────────────────────────────────── */
            #liveBadge {
                background: #c62828;
                color: #ffffff;
                border: 2px solid #ffffff;
                border-radius: 6px;
                padding: 5px 12px;
                font-family: "Consolas", monospace;
                font-size: 13px;
                font-weight: bold;
                letter-spacing: 2px;
            }

            /* ── Capture bar ──────────────────────────────────────────── */
            #captureBar {
                background: #ffffff;
                border-top: 1px solid #cbd1d9;
            }

            /* ── Mode toggle buttons ──────────────────────────────────── */
            QPushButton#modeActive {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 16px;
            }
            QPushButton#modeInactive {
                background: #ffffff;
                color: #52606d;
                border: 2px solid #cbd1d9;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 600;
                padding: 6px 16px;
            }
            QPushButton#modeInactive:hover {
                background: #e3f2fd;
                color: #1565c0;
                border-color: #1565c0;
            }

            /* ── Upload button ────────────────────────────────────────── */
            #uploadBtn {
                background: #2e7d32;
                color: #ffffff;
                border: 2px solid #1b5e20;
                border-radius: 8px;
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 12px;
            }
            #uploadBtn:hover:!disabled {
                background: #388e3c;
                border-color: #1b5e20;
            }
            #uploadBtn:pressed {
                background: #1b5e20;
            }
            #uploadBtn:disabled {
                color: #a8b0ba;
                border-color: #cbd1d9;
                background: #eef0f3;
            }

            #captureBtn {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 8px;
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 12px;
            }
            #captureBtn:hover:!disabled {
                background: #1976d2;
                border-color: #0d47a1;
            }
            #captureBtn:pressed {
                background: #0d47a1;
            }
            #captureBtn:disabled {
                color: #a8b0ba;
                border-color: #cbd1d9;
                background: #eef0f3;
            }

            /* ── Info panel ───────────────────────────────────────────── */
            #infoPanel {
                background: #eef0f3;
                margin: 10px 10px 10px 6px;
            }

            /* ── Cards ────────────────────────────────────────────────── */
            #card {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 8px;
            }
            #cardTitle {
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #52606d;
                text-transform: uppercase;
            }

            /* ── Counters ─────────────────────────────────────────────── */
            #counterBox {
                background: #f7f8fa;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
            }
            #counterLabel {
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #52606d;
            }
            #counterValue {
                font-family: "Segoe UI", "Consolas", monospace;
                font-size: 56px;
                font-weight: bold;
                line-height: 1;
            }
            #ngRate {
                font-family: "Consolas", monospace;
                font-size: 16px;
                font-weight: bold;
                color: #1a1d23;
            }
            #expectedLabel {
                font-family: "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                color: #1a1d23;
            }
            #missingLabel {
                font-size: 13px;
                font-weight: bold;
                padding: 4px;
            }

            /* ── Secondary / Reset buttons ────────────────────────────── */
            #secondaryBtn {
                background: #ffffff;
                color: #1565c0;
                border: 2px solid #1565c0;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 0.5px;
                padding: 10px;
            }
            #secondaryBtn:hover {
                background: #e3f2fd;
            }
            #secondaryBtn:pressed {
                background: #bbdefb;
            }
            #resetBtn {
                background: #ffffff;
                color: #52606d;
                border: 2px solid #a8b0ba;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 0.5px;
                padding: 10px;
            }
            #resetBtn:hover {
                background: #fff3e0;
                color: #ef6c00;
                border-color: #ef6c00;
            }
            #resetBtn:pressed {
                background: #ffe0b2;
            }

            /* ── Verdict badges (last result) ─────────────────────────── */
            QLabel[objectName="verdictBadge_OK"] {
                background: #2e7d32;
                color: #ffffff;
                border: 2px solid #1b5e20;
                border-radius: 8px;
                font-size: 22px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 8px 0;
            }
            QLabel[objectName="verdictBadge_NG"] {
                background: #c62828;
                color: #ffffff;
                border: 2px solid #8b1e1e;
                border-radius: 8px;
                font-size: 22px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 8px 0;
            }

            /* ── Utility colours ─────────────────────────────────────── */
            #dimText  { color: #7b8794; font-size: 13px; }
            #okText   { color: #2e7d32; font-size: 14px; font-weight: bold; }
            #ngText   { color: #c62828; font-size: 14px; font-weight: bold; }

            /* ── History list ─────────────────────────────────────────── */
            #historyList {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-family: "Consolas", monospace;
                font-size: 12px;
            }
            #historyList::item {
                padding: 7px 4px;
                border-bottom: 1px solid #eef0f3;
            }

            /* ── Splitter handle ─────────────────────────────────────── */
            QSplitter::handle {
                background: #cbd1d9;
                width: 2px;
            }

            /* ── Scrollbars (bigger for touch) ─────────────────────────── */
            QScrollBar:vertical {
                background: #eef0f3;
                width: 14px;
                margin: 0;
                border-radius: 7px;
            }
            QScrollBar::handle:vertical {
                background: #a8b0ba;
                border-radius: 7px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover {
                background: #52606d;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }

            /* ── Labels ───────────────────────────────────────────────── */
            QLabel { color: #1a1d23; }

            /* ── Dialogs (inherit bigger fonts) ──────────────────────── */
            QDialog, QMessageBox, QInputDialog {
                background: #ffffff;
                color: #1a1d23;
                font-size: 14px;
            }
            QDialog QPushButton, QMessageBox QPushButton, QInputDialog QPushButton {
                min-height: 34px;
                min-width: 90px;
                padding: 6px 16px;
                font-size: 14px;
                font-weight: bold;
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 6px;
            }
            QDialog QPushButton:hover, QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {
                background: #1976d2;
            }
        """)
