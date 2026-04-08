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

from PySide6.QtCore    import Qt, QTimer, Slot
from PySide6.QtGui     import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)

from batch_state import BatchStateManager
from database    import DatabaseManager
from pipeline    import CameraWorker
from ui.frame_widget  import FrameWidget
from ui.db_viewer     import DbViewerDialog

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
CAMERA_INDEX     = 0
TRIGGER_MODE     = "manual"    # "manual" | "timer" | "gpio"
TIMER_INTERVAL   = 6.0
RESULT_VIEW_SECS = 4           # seconds to show result before returning to live

# ── Status colours (match CSS variables) ──────────────────────────────────
_STATUS_COLORS = {
    "idle":       "#546e7a",
    "scanning":   "#29b6f6",
    "processing": "#ffa726",
    "connected":  "#00e676",
    "error":      "#ff1744",
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

        # ── Backend singletons ─────────────────────────────────────────────
        self._db          = DatabaseManager()
        self._batch_state = BatchStateManager(db=self._db)
        self._worker      = CameraWorker(
            batch_state    = self._batch_state,
            db             = self._db,
            camera_index   = CAMERA_INDEX,
            trigger_mode   = TRIGGER_MODE,
            timer_interval = TIMER_INTERVAL,
        )

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
        self._worker.frame_ready.connect(self._on_frame_ready)
        self._worker.result_ready.connect(self._on_result)
        self._worker.status_changed.connect(self._on_status)
        self._worker.error_occurred.connect(self._on_worker_error)

        # ── Load existing history + sync counters ──────────────────────────
        state = self._batch_state.get_state()
        self._update_counters(state)
        self._batch_id_label.setText(state["id"])
        self._load_history(state["id"])

        # ── Start worker ───────────────────────────────────────────────────
        self._worker.start()
        logger.info("CameraWorker started.")

    # ══════════════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self._build_main_area(), stretch=1)

        self.setCentralWidget(root)

    # ── Header ─────────────────────────────────────────────────────────────

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(56)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(16)

        # Left — App title
        title = QLabel("🔍  PIPE INSPECTOR")
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

        db_btn = QPushButton("🗄  DB")
        db_btn.setObjectName("dbBtn")
        db_btn.setFixedHeight(30)
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

        # Frame view area (relative container for LIVE badge overlay)
        view_container = QWidget()
        view_container.setObjectName("viewContainer")
        view_layout = QVBoxLayout(view_container)
        view_layout.setContentsMargins(0, 0, 0, 0)

        self._frame_widget = FrameWidget()
        view_layout.addWidget(self._frame_widget)

        # LIVE badge — absolute positioned over the frame widget
        self._live_badge = QLabel("● LIVE", view_container)
        self._live_badge.setObjectName("liveBadge")
        self._live_badge.move(12, 12)
        self._live_badge.setVisible(False)   # shown after camera opens

        layout.addWidget(view_container, stretch=1)

        # Mode toggle + action button bar
        capture_bar = QFrame()
        capture_bar.setObjectName("captureBar")
        capture_bar.setFixedHeight(100)
        bar_layout = QVBoxLayout(capture_bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(6)

        # ── Mode toggle row ────────────────────────────────────────────────
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)

        self._camera_mode_btn = QPushButton("📷  Camera")
        self._camera_mode_btn.setObjectName("modeActive")
        self._camera_mode_btn.setFixedHeight(28)
        self._camera_mode_btn.clicked.connect(self._set_camera_mode)

        self._upload_mode_btn = QPushButton("🖼  Upload Image")
        self._upload_mode_btn.setObjectName("modeInactive")
        self._upload_mode_btn.setFixedHeight(28)
        self._upload_mode_btn.clicked.connect(self._set_upload_mode)

        toggle_row.addWidget(self._camera_mode_btn)
        toggle_row.addWidget(self._upload_mode_btn)
        toggle_row.addStretch()
        bar_layout.addLayout(toggle_row)

        # ── Action button (swaps between Camera / Upload) ──────────────────
        self._capture_btn = QPushButton("📷   Capture & Inspect")
        self._capture_btn.setObjectName("captureBtn")
        self._capture_btn.setFixedHeight(44)
        self._capture_btn.clicked.connect(self._trigger_capture)
        bar_layout.addWidget(self._capture_btn)

        self._upload_btn = QPushButton("🖼   Upload & Inspect")
        self._upload_btn.setObjectName("uploadBtn")
        self._upload_btn.setFixedHeight(44)
        self._upload_btn.clicked.connect(self._upload_and_inspect)
        self._upload_btn.setVisible(False)
        bar_layout.addWidget(self._upload_btn)

        layout.addWidget(capture_bar)
        return panel

    # ── Right panel (counters + last result + history) ─────────────────────

    def _build_info_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("infoPanel")
        panel.setFixedWidth(370)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setSpacing(8)

        # ── Batch counters card ────────────────────────────────────────────
        counters_card = self._make_card("BATCH COUNTERS")
        c_layout = counters_card.layout()

        nums_row = QHBoxLayout()
        nums_row.setSpacing(10)

        self._counter_total = self._make_big_counter("TOTAL", "#e8eaf0")
        self._counter_ng    = self._make_big_counter("NG",    "#ff1744")
        nums_row.addWidget(self._counter_total[0])
        nums_row.addWidget(self._counter_ng[0])
        c_layout.addLayout(nums_row)

        # NG Rate row
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("NG Rate"))
        self._ng_rate_label = QLabel("—")
        self._ng_rate_label.setObjectName("ngRate")
        self._ng_rate_label.setAlignment(Qt.AlignRight)
        rate_row.addWidget(self._ng_rate_label, stretch=1)
        c_layout.addLayout(rate_row)

        # Reset button
        self._reset_btn = QPushButton("↺   Reset Batch")
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

        # Update batch counters
        self._update_counters(result["batch"])

        # Update last result card
        self._update_last_result(result)

        # Prepend to history
        self._prepend_history_row(result)

        # Flash capture button
        verdict = result["verdict"]
        flash_color = "#00e676" if verdict == "OK" else "#ff1744"
        self._capture_btn.setStyleSheet(
            f"#captureBtn {{ border: 2px solid {flash_color}; }}"
        )
        QTimer.singleShot(600, lambda: self._capture_btn.setStyleSheet(""))

        # Re-enable active button
        self._capture_btn.setEnabled(True)
        self._capture_btn.setText("📷   Capture & Inspect")
        self._upload_btn.setEnabled(True)
        self._upload_btn.setText("🖼   Upload & Inspect")

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
        color = _STATUS_COLORS.get(state, "#546e7a")
        self._status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self._status_text.setText(labels.get(state, state.title()))

        # Animate capture button during processing
        if state == "processing":
            self._capture_btn.setEnabled(False)
            self._capture_btn.setText("⏳  Processing…")
        elif state == "scanning":
            self._capture_btn.setEnabled(False)
            self._capture_btn.setText("🔍  Scanning…")

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
        self._status_dot.setStyleSheet(f"color: {_STATUS_COLORS['error']}; font-size: 14px;")
        self._status_text.setText("Error")
        self._capture_btn.setEnabled(False)
        self._upload_btn.setEnabled(True)
        self._upload_btn.setText("🖼   Upload & Inspect")

    # ══════════════════════════════════════════════════════════════════════
    # UI update helpers
    # ══════════════════════════════════════════════════════════════════════

    def _return_to_live(self) -> None:
        """Called by _result_timer after RESULT_VIEW_SECS seconds."""
        self._in_result_view = False
        self._live_badge.setVisible(True)

    def _update_counters(self, batch: dict) -> None:
        """Refresh TOTAL / NG / NG Rate from a batch snapshot dict."""
        total = batch.get("total", 0)
        ng    = batch.get("ng", 0)

        self._counter_total[1].setText(str(total))
        self._counter_ng[1].setText(str(ng))

        if total > 0:
            rate = ng / total * 100
            self._ng_rate_label.setText(f"{rate:.1f} %")
        else:
            self._ng_rate_label.setText("—")

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

        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            time_str = dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            time_str = "—"

        detail = confs[0]["label"].replace("_", " ") if confs else "No defect"
        text   = f"  {'✅' if verdict == 'OK' else '❌'}  {piece_id:<20}  {detail:<18}  {time_str}"

        item = QListWidgetItem(text)
        item.setFont(QFont("Consolas", 10))
        if verdict == "NG":
            item.setForeground(QColor("#ff1744"))
            item.setBackground(QColor("#1f1520"))
        else:
            item.setForeground(QColor("#00e676"))
            item.setBackground(QColor("#0a1f14"))

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
                "verdict":    row["verdict"],
                "piece_id":   row["piece_id"],
                "detections": row["detections"],
                "timestamp":  row["timestamp"],
            })
        if not rows:
            placeholder = QListWidgetItem("  No inspections in this batch yet.")
            placeholder.setForeground(QColor("#4a5070"))
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
        self._upload_btn.setText("🖼   Upload & Inspect")
        self._live_badge.setVisible(False)
        self._frame_widget.show_placeholder("Upload mode — กดปุ่มด้านล่างเพื่อเลือกรูปภาพ")
        logger.info("Mode: Upload Image")

    def _trigger_capture(self) -> None:
        """Fire a manual inspection trigger."""
        self._capture_btn.setEnabled(False)
        self._capture_btn.setText("🔍  Scanning…")
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
        self._upload_btn.setText("⏳  Processing…")

        def _run():
            try:
                self._worker.inspect_file(path)
            finally:
                self._file_inspecting = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _reset_batch(self) -> None:
        """Reset batch counters and return to live view."""
        new_state = self._batch_state.reset()
        self._update_counters(new_state)
        self._history_list.clear()
        self._frame_widget.show_placeholder("Batch reset — กดปุ่ม Capture เพื่อเริ่มใหม่")

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
        self._db.close()
        logger.info("MainWindow: shutdown complete.")
        event.accept()

    # ══════════════════════════════════════════════════════════════════════
    # Stylesheet
    # ══════════════════════════════════════════════════════════════════════

    def _apply_stylesheet(self) -> None:
        """Dark theme matching the web dashboard colour tokens."""
        self.setStyleSheet("""
            /* ── Global ───────────────────────────────────────────────── */
            QMainWindow, QWidget {
                background: #0d0f14;
                color: #e8eaf0;
                font-family: "Segoe UI", "Inter", system-ui, sans-serif;
                font-size: 13px;
            }

            /* ── Header ───────────────────────────────────────────────── */
            #header {
                background: #141720;
                border-bottom: 1px solid #2a2f45;
            }
            #appTitle {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #e8eaf0;
            }
            #batchId {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 12px;
                font-weight: bold;
                color: #29b6f6;
                background: #29b6f611;
                border: 1px solid #29b6f633;
                padding: 2px 10px;
                border-radius: 12px;
            }
            #statusText {
                font-size: 12px;
                color: #7a82a0;
            }
            #dbBtn {
                background: transparent;
                color: #7a82a0;
                border: 1px solid #2a2f45;
                border-radius: 12px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 11px;
                font-weight: bold;
                padding: 2px 12px;
            }
            #dbBtn:hover {
                background: #1c1f2e;
                color: #e8eaf0;
                border-color: #3a4060;
            }

            /* ── Frame panel ──────────────────────────────────────────── */
            #framePanel {
                background: #141720;
                border: 1px solid #2a2f45;
                border-radius: 8px;
                margin: 8px 4px 8px 8px;
            }
            #viewContainer {
                background: #0d0f14;
                border-radius: 6px;
            }

            /* ── LIVE badge ───────────────────────────────────────────── */
            #liveBadge {
                background: rgba(26, 8, 8, 0.85);
                color: #ff1744;
                border: 1px solid #ff1744;
                border-radius: 12px;
                padding: 2px 10px;
                font-family: "Consolas", monospace;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 2px;
            }

            /* ── Capture bar ──────────────────────────────────────────── */
            #captureBar {
                background: #141720;
                border-top: 1px solid #2a2f45;
            }

            /* ── Mode toggle buttons ──────────────────────────────────── */
            QPushButton#modeActive {
                background: #1c2a3a;
                color: #60b4ff;
                border: 1px solid #2a5a8c;
                border-radius: 4px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 11px;
                font-weight: bold;
                padding: 2px 12px;
            }
            QPushButton#modeInactive {
                background: transparent;
                color: #4a5070;
                border: 1px solid #2a2f45;
                border-radius: 4px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 11px;
                padding: 2px 12px;
            }
            QPushButton#modeInactive:hover {
                background: #1c1f2e;
                color: #7a82a0;
                border-color: #3a4060;
            }

            /* ── Upload button ────────────────────────────────────────── */
            #uploadBtn {
                background: #1a3a2c;
                color: #60ffb4;
                border: 1px solid #2a8c5a;
                border-radius: 4px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 10px;
            }
            #uploadBtn:hover:!disabled {
                background: #1e4a38;
                border-color: #3abc7a;
                color: #90ffd0;
            }
            #uploadBtn:disabled {
                opacity: 0.45;
                color: #7a82a0;
                border-color: #2a2f45;
                background: #141720;
            }

            #captureBtn {
                background: #1a3a5c;
                color: #60b4ff;
                border: 1px solid #2a5a8c;
                border-radius: 4px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 10px;
            }
            #captureBtn:hover:!disabled {
                background: #1e4a72;
                border-color: #3a7abc;
                color: #90ccff;
            }
            #captureBtn:pressed {
                background: #152e48;
            }
            #captureBtn:disabled {
                opacity: 0.45;
                color: #7a82a0;
                border-color: #2a2f45;
                background: #141720;
            }

            /* ── Info panel ───────────────────────────────────────────── */
            #infoPanel {
                background: #0d0f14;
                margin: 8px 8px 8px 4px;
            }

            /* ── Cards ────────────────────────────────────────────────── */
            #card {
                background: #1c1f2e;
                border: 1px solid #2a2f45;
                border-radius: 8px;
            }
            #cardTitle {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #7a82a0;
                text-transform: uppercase;
            }

            /* ── Counters ─────────────────────────────────────────────── */
            #counterBox {
                background: #141720;
                border: 1px solid #2a2f45;
                border-radius: 4px;
            }
            #counterLabel {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #7a82a0;
            }
            #counterValue {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 42px;
                font-weight: bold;
                line-height: 1;
            }
            #ngRate {
                font-family: "Consolas", monospace;
                font-size: 13px;
                font-weight: bold;
            }

            /* ── Reset button ─────────────────────────────────────────── */
            #resetBtn {
                background: transparent;
                color: #7a82a0;
                border: 1px solid #2a2f45;
                border-radius: 4px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 7px;
            }
            #resetBtn:hover {
                background: #2a2f45;
                color: #e8eaf0;
                border-color: #3a4060;
            }

            /* ── Verdict badges (last result) ─────────────────────────── */
            QLabel[objectName="verdictBadge_OK"] {
                background: #00e67622;
                color: #00e676;
                border: 1px solid #00e676;
                border-radius: 12px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 20px;
                font-weight: bold;
                padding: 6px 0;
            }
            QLabel[objectName="verdictBadge_NG"] {
                background: #ff174422;
                color: #ff1744;
                border: 1px solid #ff1744;
                border-radius: 12px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 20px;
                font-weight: bold;
                padding: 6px 0;
            }

            /* ── Utility colours ─────────────────────────────────────── */
            #dimText  { color: #4a5070; font-size: 11px; }
            #okText   { color: #00e676; font-size: 12px; }
            #ngText   { color: #ff1744; font-size: 12px; font-weight: bold; }

            /* ── History list ─────────────────────────────────────────── */
            #historyList {
                background: #0d0f14;
                border: none;
                font-family: "Consolas", monospace;
                font-size: 11px;
            }
            #historyList::item {
                padding: 4px 2px;
                border-left: 2px solid transparent;
            }
            #historyList::item:selected {
                background: #1c1f2e;
            }

            /* ── Splitter handle ─────────────────────────────────────── */
            QSplitter::handle {
                background: #2a2f45;
                width: 1px;
            }

            /* ── Scrollbars ───────────────────────────────────────────── */
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #2a2f45;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)
