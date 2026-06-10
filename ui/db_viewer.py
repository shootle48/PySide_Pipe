"""
db_viewer.py
────────────
QDialog แสดงข้อมูล Database — เทียบเท่า db-viewer.html ในเวอร์ชัน Web

Layout:
  ┌────────────────────────────────────────────────────┐
  │  DATABASE VIEWER                  [🔄 Refresh]     │
  ├─────────────────────┬──────────────────────────────┤
  │  BATCHES            │  INSPECTIONS                 │
  │  ┌───────────────┐  │  ┌────────────────────────┐  │
  │  │ BATCH-xxx  50 │  │  │ Piece ID │ Verdict │... │  │
  │  │ BATCH-xxx  12 │  │  │ ...      │         │    │  │
  │  └───────────────┘  │  └────────────────────────┘  │
  │                     │  Total: 50  NG: 5  Rate: 10% │
  │                     │  [📄 Export CSV]              │
  └─────────────────────┴──────────────────────────────┘
"""

from __future__ import annotations

import base64
import binascii
import csv
import logging
import re
from datetime import datetime
from pathlib import Path

from datetime import datetime, time, timedelta, timezone

from PySide6.QtCore    import QDate, QLocale, Qt, QThread, Signal, Slot
from PySide6.QtGui     import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QDateEdit, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QFrame,
)

from core.constants import MONO_FONT, VERDICT_COLORS
from core.utils     import iso_to_local_str

# จำนวน inspection ต่อหน้า (แบ่งหน้าเพื่อไม่โหลดทั้งหมดเข้า RAM)
_PAGE_SIZE = 10

# QSS ขยายปฏิทินให้จิ้มง่ายบน touchscreen (cells/buttons ใหญ่ขึ้น)
_CALENDAR_QSS = """
QCalendarWidget QAbstractItemView {
    font-size: 18px;
    selection-background-color: #1565c0;
    selection-color: #ffffff;
}
QCalendarWidget QWidget#qt_calendar_navigationbar { min-height: 44px; }
QCalendarWidget QToolButton { min-height: 40px; min-width: 44px; font-size: 16px; icon-size: 28px; }
QCalendarWidget QSpinBox { font-size: 16px; min-height: 32px; }
"""

# QSS pill verdict — ใส่ตรงบน QLabel เอง (inline) ไม่พึ่ง cascade จาก dialog
_PILL_QSS_OK = (
    "QLabel { background:#c8e6c9; color:#1b5e20; border-radius:12px;"
    " padding:3px 16px; font-weight:bold; font-size:13px; }"
)
_PILL_QSS_NG = (
    "QLabel { background:#ffcdd2; color:#b71c1c; border-radius:12px;"
    " padding:3px 16px; font-weight:bold; font-size:13px; }"
)

logger = logging.getLogger(__name__)


class DbViewerDialog(QDialog):
    """
    Database viewer dialog — shows all batches and their inspections.

    Usage:
        dialog = DbViewerDialog(db=db_manager, parent=main_window)
        dialog.exec()
    """

    def __init__(self, db, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db             = db
        self._current_batch  = None   # currently selected batch id

        # ── Pagination + date-filter state ─────────────────────────────────
        self._page       = 0
        self._page_size  = _PAGE_SIZE
        self._total      = 0
        self._date_from: "str | None" = None 
        self._date_to:   "str | None" = None

        self.setWindowTitle("Database Viewer")

        self._build_ui()
        self._apply_stylesheet()
        self._load_batches()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.showFullScreen()

    # ══════════════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Header bar ─────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel("DATABASE VIEWER")
        title.setObjectName("dialogTitle")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.setFixedHeight(44)
        refresh_btn.setMinimumWidth(120)
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)

        root.addLayout(header)

        # ── Divider ────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("divider")
        root.addWidget(line)

        # ── Top bar: date filter (ซ้าย) + pagination (ขวา) ─────────────
        root.addWidget(self._build_topbar())

        # ── Main splitter (batches left / inspections right) ───────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_batch_panel())
        splitter.addWidget(self._build_inspection_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([240, 560, 460])

        root.addWidget(splitter, stretch=1)

        # ── Big CLOSE button (easy touch target, factory use) ──────────
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("CLOSE")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedHeight(60)
        close_btn.setMinimumWidth(240)
        close_btn.setToolTip("ปิดหน้าต่าง Database Viewer  (Esc)")
        close_btn.setShortcut("Esc")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        close_row.addStretch(1)
        root.addLayout(close_row)

    # ── Top bar: date filter + pagination ───────────────────────────────

    def _enlarge_calendar(self, date_edit: QDateEdit) -> None:
        """ขยายปฏิทิน popup ให้จิ้มง่ายบน touchscreen (ไม่แตะ locale — เลขยังอารบิก)"""
        cal = date_edit.calendarWidget()
        cal.setMinimumSize(420, 360)
        cal.setFont(QFont("Segoe UI", 14))
        cal.setGridVisible(True)
        cal.setStyleSheet(_CALENDAR_QSS)

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        # ── Date filter (ซ้าย) — กดเลือกวันได้เลย กรองทันที ──────────────
        _en_locale = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)

        row.addWidget(QLabel("กรองวันที่:"))

        self._date_from_edit = QDateEdit()
        self._date_from_edit.setCalendarPopup(True)
        self._date_from_edit.setDisplayFormat("dd/MM/yyyy")
        self._date_from_edit.setDate(QDate.currentDate().addDays(-7))
        self._date_from_edit.setLocale(_en_locale)
        self._enlarge_calendar(self._date_from_edit)
        self._date_from_edit.dateChanged.connect(self._on_apply_filter)
        row.addWidget(self._date_from_edit)

        dash = QLabel("–")
        row.addWidget(dash)

        self._date_to_edit = QDateEdit()
        self._date_to_edit.setCalendarPopup(True)
        self._date_to_edit.setDisplayFormat("dd/MM/yyyy")
        self._date_to_edit.setDate(QDate.currentDate())
        self._date_to_edit.setLocale(_en_locale)
        self._enlarge_calendar(self._date_to_edit)
        self._date_to_edit.dateChanged.connect(self._on_apply_filter)
        row.addWidget(self._date_to_edit)
        
        self._clear_filter_btn = QPushButton("รีเซ็ต")
        self._clear_filter_btn.setObjectName("secondaryBtn")
        self._clear_filter_btn.setFixedHeight(34)
        self._clear_filter_btn.clicked.connect(self._on_clear_filter)
        row.addWidget(self._clear_filter_btn)

        row.addStretch()

        # ── Pagination (ขวา) ────────────────────────────────────────────
        self._page_label = QLabel("—")
        self._page_label.setObjectName("showingLabel")
        row.addWidget(self._page_label)

        self._prev_btn = QPushButton("◀")
        self._prev_btn.setObjectName("pageIconBtn")
        self._prev_btn.setFixedSize(40, 36)
        self._prev_btn.clicked.connect(self._on_prev_page)
        row.addWidget(self._prev_btn)

        self._next_btn = QPushButton("▶")
        self._next_btn.setObjectName("pageIconBtn")
        self._next_btn.setFixedSize(40, 36)
        self._next_btn.clicked.connect(self._on_next_page)
        row.addWidget(self._next_btn)

        return bar

    # ── Left: batch list ────────────────────────────────────────────────

    def _build_batch_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(6)

        label = QLabel("BATCHES")
        label.setObjectName("sectionTitle")
        layout.addWidget(label)

        self._batch_list = QListWidget()
        self._batch_list.setObjectName("batchList")
        self._batch_list.currentItemChanged.connect(self._on_batch_selected)
        layout.addWidget(self._batch_list, stretch=1)

        delete_batch_btn = QPushButton("ลบ Batch นี้")
        delete_batch_btn.setObjectName("dangerBtn")
        delete_batch_btn.setFixedHeight(44)
        delete_batch_btn.clicked.connect(self._delete_current_batch)
        layout.addWidget(delete_batch_btn)

        return panel

    # ── Middle: inspection table ────────────────────────────────────────

    def _build_inspection_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        # Header: title + OK/NG chips
        head = QHBoxLayout()
        title = QLabel("Inspection History")
        title.setObjectName("panelHeader")
        head.addWidget(title)
        head.addStretch()
        self._ok_chip = QLabel("OK: —")
        self._ok_chip.setObjectName("okChip")
        head.addWidget(self._ok_chip)
        self._ng_chip = QLabel("NG: —")
        self._ng_chip.setObjectName("ngChip")
        head.addWidget(self._ng_chip)
        layout.addLayout(head)

        # Table (4 cols — no Size)
        self._table = QTableWidget()
        self._table.setObjectName("inspectionTable")
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["No.", "Verdict", "Defects", "Timestamp"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        # แถวสูงพอให้ verdict pill (cellWidget) แสดงเต็มไม่ถูก clip + จิ้ม touch ง่าย
        self._table.verticalHeader().setDefaultSectionSize(48)
        self._table.setColumnWidth(0, 70)
        self._table.setColumnWidth(1, 100)
        self._table.setColumnWidth(2, 200)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self._table, stretch=1)

        # Action buttons row (Clear / Delete / Export)
        actions = QHBoxLayout()
        actions.setSpacing(10)

        clear_img_btn = QPushButton("Clear Image")
        clear_img_btn.setObjectName("secondaryBtn")
        clear_img_btn.setFixedHeight(40)
        clear_img_btn.setToolTip("เคลียร์รูปของ record ที่เลือก (เก็บ metadata ไว้)")
        clear_img_btn.clicked.connect(self._clear_selected_image)
        actions.addWidget(clear_img_btn)

        delete_row_btn = QPushButton("ลบ Record")
        delete_row_btn.setObjectName("dangerBtn")
        delete_row_btn.setFixedHeight(40)
        delete_row_btn.setToolTip("ลบ inspection record ที่เลือกออกทั้งหมด")
        delete_row_btn.clicked.connect(self._delete_selected_record)
        actions.addWidget(delete_row_btn)

        actions.addStretch()

        export_btn = QPushButton("Export CSV")
        export_btn.setObjectName("secondaryBtn")
        export_btn.setFixedHeight(40)
        export_btn.clicked.connect(self._export_csv)
        actions.addWidget(export_btn)

        export_ds_btn = QPushButton("Export Dataset")
        export_ds_btn.setObjectName("secondaryBtn")
        export_ds_btn.setFixedHeight(40)
        export_ds_btn.setToolTip("Export รูป + annotations สำหรับทำ dataset train model")
        export_ds_btn.clicked.connect(self._export_dataset)
        actions.addWidget(export_ds_btn)

        layout.addLayout(actions)
        return panel

    # ── Right: NG image preview ─────────────────────────────────────────

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("previewPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Header: title + ID chip
        head = QHBoxLayout()
        title = QLabel("NG Image Preview")
        title.setObjectName("panelHeader")
        head.addWidget(title)
        head.addStretch()
        self._id_chip = QLabel("ID: —")
        self._id_chip.setObjectName("idChip")
        head.addWidget(self._id_chip)
        layout.addLayout(head)

        # Image area
        self._preview_label = QLabel()
        self._preview_label.setObjectName("imagePreview")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setText("— เลือก row ที่เป็น NG เพื่อดูภาพ —")
        self._preview_label.setMinimumHeight(240)
        layout.addWidget(self._preview_label, stretch=1)
        self._current_pixmap: QPixmap | None = None

        # Footer: DEFECT TYPE + ขยายรูป
        footer = QHBoxLayout()
        dt_cap = QLabel("DEFECT TYPE")
        dt_cap.setObjectName("footerCaption")
        footer.addWidget(dt_cap)
        self._defect_type_label = QLabel("—")
        self._defect_type_label.setObjectName("defectTypeValue")
        footer.addWidget(self._defect_type_label)
        footer.addStretch()
        self._fullscreen_btn = QPushButton("ขยายรูป")
        self._fullscreen_btn.setObjectName("secondaryBtn")
        self._fullscreen_btn.setFixedHeight(36)
        self._fullscreen_btn.setMinimumWidth(110)
        self._fullscreen_btn.setEnabled(False)
        self._fullscreen_btn.clicked.connect(self._open_fullscreen)
        footer.addWidget(self._fullscreen_btn)
        layout.addLayout(footer)

        return panel

    # ══════════════════════════════════════════════════════════════════════
    # Data loading
    # ══════════════════════════════════════════════════════════════════════

    def _load_batches(self) -> None:
        """Load all batches from DB into the left list."""
        self._batch_list.clear()
        batches = self._db.get_all_batches()

        for b in batches:
            active_mark = "  ●" if b["is_active"] else ""
            # แปลงเป็น local time ให้ตรงกับ timestamp ของชิ้น (ไม่งั้นเหลื่อม UTC 7 ชม.)
            started = iso_to_local_str(b["started_at"], fmt="%Y-%m-%d %H:%M")
            text = (
                f"{b['id']}{active_mark}\n"
                f"  {started}   Total: {b['total']}   NG: {b['ng']}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, b["id"])
            item.setFont(QFont(MONO_FONT, 12))
            if b["is_active"]:
                item.setForeground(QColor("#1565c0"))
            self._batch_list.addItem(item)

        if self._batch_list.count() > 0:
            self._batch_list.setCurrentRow(0)

    # ── Stats / rate helpers ───────────────────────────────────────────────

    @staticmethod
    def _batch_ng_rate_str(total: int, ng: int) -> str:
        """OEE Quality Rate = (total - ng) / total × 100.  Returns '—' when total=0."""
        if total <= 0:
            return "—"
        return f"{(total - ng) / total * 100:.1f}%"

    def _populate_inspection_table(self, rows: list, offset: int = 0) -> None:
        """Fill the inspection QTableWidget from a list of row dicts.

        offset = ลำดับเริ่มต้นของหน้านี้ (ทำให้ No. ต่อเนื่องข้ามหน้า)
        ไม่เก็บ image_b64 แล้ว — โหลด lazy ตอนคลิกผ่าน get_inspection_image()
        """
        self._table.setRowCount(0)

        for row_data in rows:
            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)

            no_item = QTableWidgetItem(str(offset + row_idx + 1))
            no_item.setFont(QFont(MONO_FONT, 12))
            no_item.setTextAlignment(Qt.AlignCenter)
            no_item.setData(Qt.UserRole, row_data["piece_id"])  # เก็บ piece_id ไว้ใช้ delete/clear
            self._table.setItem(row_idx, 0, no_item)

            # Verdict — pill (cellWidget) ใส่ stylesheet ตรงบน pill เอง (inline)
            # ไม่พึ่ง QSS cascade จาก dialog (เพี้ยนบน Windows) และ cellWidget ไม่โดน
            # ::item override (ต่างจาก setBackground บน QTableWidgetItem ที่ Qt เพิกเฉย)
            verdict = row_data["verdict"]
            pill = QLabel(verdict)
            pill.setAlignment(Qt.AlignCenter)
            pill.setMinimumHeight(24)
            pill.setStyleSheet(_PILL_QSS_OK if verdict == "OK" else _PILL_QSS_NG)
            wrap = QWidget()
            wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            # โปร่งใส — ไม่ให้พื้นเทา global ขึ้นเป็นกล่องหลัง pill
            # (ปลอดภัยแล้ว เพราะ pill ใช้ inline QSS ไม่พึ่ง cascade จาก dialog)
            wrap.setStyleSheet("background: transparent;")
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(8, 4, 8, 4)
            wl.addWidget(pill)
            self._table.setCellWidget(row_idx, 1, wrap)

            dets = row_data.get("detections", [])
            det_text = ", ".join(d.get("label", "unknown") for d in dets) if dets else "—"
            self._set_cell(row_idx, 2, det_text)

            ts_str = iso_to_local_str(row_data["timestamp"], fmt="%Y-%m-%d %H:%M:%S")
            self._set_cell(row_idx, 3, ts_str)

    def _load_inspections(self, batch_id: str) -> None:
        """เลือก batch ใหม่ → กลับไปหน้าแรก แล้วโหลด"""
        self._current_batch = batch_id
        self._page = 0
        self._reload_page()

    # ── Date filter helpers ─────────────────────────────────────────────────

    @staticmethod
    def _qdate_to_utc_iso(qdate: QDate, end_of_day: bool = False) -> str:
        """แปลง QDate (local) → UTC ISO string สำหรับ query

        end_of_day=False → 00:00 ของวันนั้น (lower bound, inclusive)
        end_of_day=True  → 00:00 ของวันถัดไป (upper bound, exclusive)
        """
        py_date = qdate.toPython()
        local_midnight = datetime.combine(py_date, time.min).astimezone()  # local tz
        if end_of_day:
            local_midnight = local_midnight + timedelta(days=1)   # ขอบบน exclusive
        return local_midnight.astimezone(timezone.utc).isoformat()

    def _reload_page(self) -> None:
        """โหลด inspection หน้าปัจจุบัน (LIMIT/OFFSET + date filter) — โหลดเบา"""
        if self._current_batch is None:
            return

        df, dt = self._date_from, self._date_to
        self._total = self._db.count_inspections(self._current_batch, df, dt)

        # clamp page ให้อยู่ในขอบเขต (เผื่อ filter ทำให้จำนวนหน้าลดลง)
        max_page = max(0, (self._total - 1) // self._page_size) if self._total else 0
        self._page = min(self._page, max_page)
        offset = self._page * self._page_size

        rows = self._db.get_inspections_page(
            self._current_batch, df, dt, limit=self._page_size, offset=offset,
        )
        self._populate_inspection_table(rows, offset=offset)

        # clear preview (row selection หาย)
        self._current_pixmap = None
        self._fullscreen_btn.setEnabled(False)
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText("— เลือก row ที่เป็น NG เพื่อดูภาพ —")
        self._id_chip.setText("ID: —")
        self._defect_type_label.setText("—")

        # pagination label "Showing X-Y of N records" + ปุ่ม
        start = offset + 1 if self._total else 0
        end   = offset + len(rows)
        self._page_label.setText(f"Showing {start}-{end} of {self._total} records")
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < total_pages - 1)

        # stats bar (นับจาก filter ทั้งหมด ไม่ใช่แค่หน้าปัจจุบัน)
        ng = self._db.count_inspections(self._current_batch, df, dt, verdict="NG")
        self._update_stats_bar(self._total, ng)

    # ── Pagination / filter slots ───────────────────────────────────────────

    @Slot()
    def _on_prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._reload_page()

    @Slot()
    def _on_next_page(self) -> None:
        self._page += 1
        self._reload_page()

    @Slot()
    def _on_apply_filter(self) -> None:
        """กรองทันทีเมื่อเลือกวัน (auto-apply จาก dateChanged)."""
        self._date_from = self._qdate_to_utc_iso(self._date_from_edit.date())
        self._date_to   = self._qdate_to_utc_iso(self._date_to_edit.date(), end_of_day=True)
        self._page = 0
        self._reload_page()

    @Slot()
    def _on_clear_filter(self) -> None:
        """ล้างตัวกรองวันที่ → ดูทั้งหมด (reset ช่องวันที่โดยไม่ยิง auto-apply ซ้ำ)."""
        self._date_from = None
        self._date_to   = None

        self._date_from_edit.blockSignals(True)
        self._date_to_edit.blockSignals(True)
        self._date_from_edit.setDate(QDate.currentDate().addDays(-7))
        self._date_to_edit.setDate(QDate.currentDate())
        self._date_from_edit.blockSignals(False)
        self._date_to_edit.blockSignals(False)

        self._page = 0
        self._reload_page()

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFont(QFont(MONO_FONT, 12))
        self._table.setItem(row, col, item)

    def _update_stats_bar(self, total: int, ng: int) -> None:
        """อัพเดต OK/NG chips ใน header + batch list item ซ้าย."""
        self._ok_chip.setText(f"OK: {max(0, total - ng)}")
        self._ng_chip.setText(f"NG: {ng}")

        # Patch batch list item text in-place — no full reload needed
        for i in range(self._batch_list.count()):
            item = self._batch_list.item(i)
            if item.data(Qt.UserRole) == self._current_batch:
                new_text = re.sub(r"Total: \d+", f"Total: {total}", item.text())
                new_text = re.sub(r"NG: \d+",    f"NG: {ng}",       new_text)
                item.setText(new_text)
                break

    # ══════════════════════════════════════════════════════════════════════
    # Slots
    # ══════════════════════════════════════════════════════════════════════

    @Slot()
    def _on_row_selected(self) -> None:
        """แสดง NG image เมื่อ user คลิก row ในตาราง — โหลดรูป lazy ด้วย piece_id"""
        piece_id = self._get_selected_piece_id()
        if piece_id is None:
            return

        # อัปเดต ID chip + DEFECT TYPE (จาก defects cell col 2)
        self._id_chip.setText(f"ID: {piece_id}")
        sel = self._table.selectedItems()
        if sel:
            det_item = self._table.item(self._table.row(sel[0]), 2)
            self._defect_type_label.setText(det_item.text() if det_item else "—")

        b64 = self._db.get_inspection_image(piece_id)   # lazy: query รูปเดี่ยวตอนคลิก
        if not b64:
            self._current_pixmap = None
            self._fullscreen_btn.setEnabled(False)
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("— ไม่มีภาพ (OK result หรือบันทึกก่อนอัปเดต) —")
            return

        try:
            img_bytes = base64.b64decode(b64)
            qimage    = QImage.fromData(img_bytes)
            if qimage.isNull():
                raise ValueError("QImage decode returned null")
            self._current_pixmap = QPixmap.fromImage(qimage)
        except (binascii.Error, ValueError) as exc:
            logger.error(f"DbViewer: cannot decode image: {exc}")
            self._current_pixmap = None
            self._fullscreen_btn.setEnabled(False)
            self._preview_label.setText("— โหลดภาพไม่สำเร็จ —")
            return
        self._fullscreen_btn.setEnabled(True)
        self._preview_label.setPixmap(
            self._current_pixmap.scaled(
                self._preview_label.width(),
                self._preview_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    @Slot()
    def _open_fullscreen(self) -> None:
        """เปิดภาพเต็มจอใน QDialog แยก

        Multiple close paths (สำคัญมาก — ลูกค้า factory ไม่ควรติดอยู่ในหน้านี้):
          • กดปุ่ม CLOSE ด้านล่าง  (ใหญ่ 72px — มองเห็นง่ายบน touchscreen)
          • กด Esc บน keyboard
          • แตะ/คลิกที่ตัวรูปภาพ (ทั้งพื้นที่)
          • ปุ่ม X ที่ title bar ของ window
        """
        if self._current_pixmap is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("NG Image — แตะรูปเพื่อปิด หรือกด Esc")
        dlg.showMaximized()

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Hint bar — informs user how to exit ─────────────────────────
        hint = QLabel("แตะที่รูปเพื่อปิด  •  หรือกดปุ่ม CLOSE ด้านล่าง  •  หรือกด Esc")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("""
            background: #0d47a1;
            color: #ffffff;
            font-size: 14px;
            font-weight: bold;
            letter-spacing: 1px;
            padding: 8px;
        """)
        layout.addWidget(hint)

        # ── Image area — clickable-anywhere-to-close ────────────────────
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setStyleSheet("background: #1a1d23;")   # keep dark backdrop for photo
        img_label.setCursor(Qt.PointingHandCursor)
        img_label.setToolTip("คลิกที่ภาพเพื่อปิดหน้าต่าง")
        # Click anywhere on the image closes the dialog
        img_label.mousePressEvent = lambda ev: dlg.close()
        screen = dlg.screen().availableGeometry()
        img_label.setPixmap(
            self._current_pixmap.scaled(
                screen.width(), screen.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        layout.addWidget(img_label, stretch=1)

        # ── Big CLOSE button — easy to hit on touchscreen ───────────────
        close_btn = QPushButton("CLOSE   (หรือกด Esc / แตะที่รูป)")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedHeight(72)
        close_btn.setShortcut("Esc")
        close_btn.setStyleSheet("""
            QPushButton {
                background: #1565c0;
                color: #ffffff;
                border: none;
                font-size: 18px;
                font-weight: bold;
                letter-spacing: 2px;
            }
            QPushButton:hover  { background: #1976d2; }
            QPushButton:pressed { background: #0d47a1; }
        """)
        close_btn.clicked.connect(dlg.close)
        layout.addWidget(close_btn)

        dlg.exec()

    @Slot(object, object)
    def _on_batch_selected(self, current, previous) -> None:
        if current is None:
            return
        batch_id = current.data(Qt.UserRole)
        if batch_id:
            self._load_inspections(batch_id)

    @Slot()
    def _refresh(self) -> None:
        """Reload batches and re-select the current batch."""
        prev_batch = self._current_batch
        self._load_batches()

        # Re-select same batch if possible
        if prev_batch:
            for i in range(self._batch_list.count()):
                item = self._batch_list.item(i)
                if item.data(Qt.UserRole) == prev_batch:
                    self._batch_list.setCurrentRow(i)
                    break

    def _get_selected_piece_id(self) -> str | None:
        """Return piece_id ของ row ที่เลือกใน table หรือ None"""
        selected = self._table.selectedItems()
        if not selected:
            return None
        return self._table.item(self._table.row(selected[0]), 0).data(Qt.UserRole)

    @Slot()
    def _clear_selected_image(self) -> None:
        piece_id = self._get_selected_piece_id()
        if not piece_id:
            QMessageBox.warning(self, "Clear Image", "กรุณาเลือก record ก่อน")
            return
        confirm = QMessageBox.question(
            self, "Clear Image",
            f"เคลียร์รูปของ\n{piece_id}\n\n(metadata ยังคงอยู่)",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._db.clear_image_b64(piece_id)
        # รูปโหลด lazy อยู่แล้ว — แค่เคลียร์ preview พอ (คลิกซ้ำจะขึ้น "ไม่มีภาพ")
        self._current_pixmap = None
        self._fullscreen_btn.setEnabled(False)
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText("— รูปถูกเคลียร์แล้ว —")

    @Slot()
    def _delete_selected_record(self) -> None:
        piece_id = self._get_selected_piece_id()
        if not piece_id:
            QMessageBox.warning(self, "ลบ Record", "กรุณาเลือก record ก่อน")
            return
        confirm = QMessageBox.question(
            self, "ลบ Record",
            f"ลบ inspection record\n{piece_id}\n\nไม่สามารถกู้คืนได้",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._db.delete_inspection(piece_id)
        # recalc counter ใน DB (batches table) ให้ตรง แล้ว reload หน้าปัจจุบัน
        if self._current_batch:
            self._db.recalculate_batch_counters(self._current_batch)
        self._table.clearSelection()
        self._reload_page()   # repopulate + stats + clear preview

    @Slot()
    def _delete_current_batch(self) -> None:
        if not self._current_batch:
            QMessageBox.warning(self, "ลบ Batch", "กรุณาเลือก batch ก่อน")
            return
        confirm = QMessageBox.question(
            self, "ลบ Batch",
            f"ลบ inspection ทั้งหมดใน\n{self._current_batch}\n\nไม่สามารถกู้คืนได้",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._db.delete_batch_inspections(self._current_batch)
        self._load_batches()   # refresh batch list ฝั่งซ้าย
        self._table.setRowCount(0)
        self._ok_chip.setText("OK: 0")
        self._ng_chip.setText("NG: 0")
        self._page_label.setText("Showing 0-0 of 0 records")
        self._id_chip.setText("ID: —")
        self._defect_type_label.setText("—")
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText("— เลือก row ที่เป็น NG เพื่อดูภาพ —")
        self._current_pixmap = None
        self._fullscreen_btn.setEnabled(False)

    @Slot()
    def _export_csv(self) -> None:
        """Export current inspection table to a CSV file."""
        if self._current_batch is None:
            QMessageBox.warning(self, "Export", "Select a batch first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV",
            f"{self._current_batch}_inspections.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return

        rows = self._db.get_recent_inspections(self._current_batch, limit=500)

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig = BOM for Excel
                writer = csv.writer(f)
                writer.writerow([
                    "piece_id", "verdict", "detected_size", "detections", "timestamp",
                ])
                for r in rows:
                    dets = ", ".join(d["label"] for d in r.get("detections", []))
                    writer.writerow([
                        r["piece_id"],
                        r["verdict"],
                        r.get("detected_size", "") or "",
                        dets or "—",
                        r["timestamp"],
                    ])
            QMessageBox.information(self, "Export Complete", f"Saved to:\n{path}")
        except (OSError, csv.Error) as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    @Slot()
    def _export_dataset(self) -> None:
        """Export NG images + annotations เป็น dataset พร้อม train model.

        Structure:
          dataset_export_YYYYMMDD_HHMMSS/
          ├── NG/
          │   ├── BATCH-XXXXXX-0001.jpg
          │   └── ...
          └── annotations.csv  (piece_id, batch_id, verdict,
                               detected_size, label, total, ng, timestamp)
        """
        target_dir = QFileDialog.getExistingDirectory(
            self, "เลือกโฟลเดอร์ปลายทางสำหรับ Dataset"
        )
        if not target_dir:
            return

        rows = self._db.get_all_inspections_with_images()
        if not rows:
            QMessageBox.warning(
                self, "Export Dataset",
                "ไม่พบรูปภาพใน DB — ยังไม่มี NG inspection ที่บันทึกรูปไว้"
            )
            return

        # สร้าง lookup: batch_id → (total, ng)
        batch_counters = {
            b["id"]: (b["total"], b["ng"])
            for b in self._db.get_all_batches()
        }

        ts_folder = datetime.now().strftime("dataset_export_%Y%m%d_%H%M%S")
        out_dir   = Path(target_dir) / ts_folder
        ng_dir    = out_dir / "NG"
        try:
            ng_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Export Failed", f"สร้างโฟลเดอร์ไม่สำเร็จ:\n{exc}")
            return

        saved_images = 0
        failed       = 0
        csv_rows     = []

        for r in rows:
            piece_id = r["piece_id"]
            try:
                img_bytes = base64.b64decode(r["image_b64"])
                (ng_dir / f"{piece_id}.jpg").write_bytes(img_bytes)
                saved_images += 1
            except (binascii.Error, OSError) as exc:
                logger.error(f"Failed to save {piece_id}: {exc}")
                failed += 1
                continue

            # Annotations — 1 row per inspection (รวม label หลายตัวด้วย comma)
            dets   = r.get("detections", [])
            labels = ", ".join(d.get("label", "unknown") for d in dets) if dets else ""
            total, ng = batch_counters.get(r["batch_id"], (0, 0))
            csv_rows.append([
                piece_id,
                r["batch_id"],
                r["verdict"],
                r.get("detected_size", "") or "",
                labels, total, ng, r["timestamp"],
            ])

        # Write annotations.csv
        try:
            with open(out_dir / "annotations.csv", "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "piece_id", "batch_id", "verdict", "detected_size",
                    "label", "total", "ng", "timestamp",
                ])
                writer.writerows(csv_rows)
        except (OSError, csv.Error) as exc:
            QMessageBox.critical(self, "Export Failed", f"เขียน annotations.csv ไม่สำเร็จ:\n{exc}")
            return

        msg = (
            f"Saved to:\n{out_dir}\n\n"
            f"Images saved : {saved_images}\n"
            f"Annotations  : {len(csv_rows)} rows\n"
        )
        if failed:
            msg += f"Failed       : {failed} images\n"
        QMessageBox.information(self, "Export Dataset Complete", msg)

    # ══════════════════════════════════════════════════════════════════════
    # Stylesheet
    # ══════════════════════════════════════════════════════════════════════

    def _apply_stylesheet(self) -> None:
        """Light industrial theme — สำหรับ factory touchscreen (14")"""
        self.setStyleSheet("""
            QDialog, QWidget {
                background: #eef0f3;
                color: #1a1d23;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 14px;
            }

            #dialogTitle {
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 1px;
                color: #1a1d23;
            }

            #divider {
                color: #cbd1d9;
                background: #cbd1d9;
                max-height: 1px;
            }

            #sectionTitle {
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #52606d;
                text-transform: uppercase;
            }

            /* Batch list */
            #batchList {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-family: "Consolas", monospace;
                font-size: 12px;
                padding: 2px;
            }
            #batchList::item {
                padding: 12px 10px;
                border-bottom: 1px solid #eef0f3;
                color: #1a1d23;
            }
            #batchList::item:selected {
                background: #e3f2fd;
                border-left: 4px solid #1565c0;
                color: #0d47a1;
            }
            #batchList::item:hover:!selected {
                background: #f1f3f6;
            }

            /* Inspection table */
            #inspectionTable {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                gridline-color: #e3e6eb;
                font-family: "Consolas", monospace;
                font-size: 13px;
                alternate-background-color: #f7f8fa;
                color: #1a1d23;
            }
            #inspectionTable::item {
                padding: 10px 8px;
            }
            #inspectionTable::item:selected {
                background: #e3f2fd;
                color: #0d47a1;
            }
            QHeaderView::section {
                background: #f1f3f6;
                color: #1a1d23;
                border: none;
                border-bottom: 2px solid #cbd1d9;
                border-right: 1px solid #e3e6eb;
                padding: 12px 8px;
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 1px;
                text-transform: uppercase;
            }

            /* Stats */
            #statLabel {
                font-family: "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                color: #1a1d23;
                padding: 4px 8px;
            }

            /* ── Top bar ─────────────────────────────────────────────── */
            #topBar {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            /* label ใน topBar โปร่งใส — ไม่ให้พื้นเทา global ขึ้นเป็นกล่อง */
            #topBar QLabel { background: transparent; }
            #topBar QDateEdit {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
                min-width: 110px;
            }
            #topBar QDateEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 22px;
                background: transparent;
                border-left: 1px solid #cbd1d9;
            }

            /* ── Calendar popup ──────────────────────────────────────────
               ⚠️ ต้อง re-style เพราะกฎ global "QWidget { background }" ด้านบน
               จะไหลลงไปทับ internal view ของปฏิทิน → กดเลือกวันไม่ได้     */
            QCalendarWidget QWidget {
                background: #ffffff;
                color: #1a1d23;
            }
            QCalendarWidget QAbstractItemView:enabled {
                background: #ffffff;
                color: #1a1d23;
                selection-background-color: #1565c0;
                selection-color: #ffffff;
                outline: 0;
            }
            QCalendarWidget QAbstractItemView:disabled {
                color: #cbd1d9;
            }
            QCalendarWidget QWidget#qt_calendar_navigationbar {
                background: #f7f8fa;
            }
            QCalendarWidget QToolButton {
                background: transparent;
                color: #1a1d23;
                font-size: 13px;
                font-weight: bold;
                icon-size: 18px;
                padding: 2px 6px;
            }
            QCalendarWidget QToolButton:hover {
                background: #e3f2fd;
                border-radius: 4px;
            }
            QCalendarWidget QMenu {
                background: #ffffff;
                color: #1a1d23;
            }
            QCalendarWidget QSpinBox {
                background: #ffffff;
                color: #1a1d23;
                selection-background-color: #1565c0;
                selection-color: #ffffff;
            }
            #showingLabel {
                font-size: 13px;
                color: #52606d;
                padding: 0 8px;
            }
            #pageIconBtn {
                background: #ffffff;
                color: #1565c0;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-size: 15px;
                font-weight: bold;
            }
            #pageIconBtn:hover:!disabled  { background: #e3f2fd; border-color: #1565c0; }
            #pageIconBtn:disabled         { color: #cbd1d9; }

            /* ── Panel header + chips ────────────────────────────────── */
            #panelHeader {
                background: transparent;
                font-size: 15px;
                font-weight: bold;
                color: #1a1d23;
            }
            #okChip {
                background: #e8f5e9;
                color: #2e7d32;
                border-radius: 11px;
                padding: 3px 12px;
                font-size: 12px;
                font-weight: bold;
            }
            #ngChip {
                background: #ffebee;
                color: #c62828;
                border-radius: 11px;
                padding: 3px 12px;
                font-size: 12px;
                font-weight: bold;
            }
            #idChip {
                background: transparent;
                color: #52606d;
                padding: 3px 4px;
                font-family: "Consolas", monospace;
                font-size: 12px;
                font-weight: bold;
            }

            /* ── Verdict pills (table cellWidget) ────────────────────── */
            #verdictPillOK {
                background: #e8f5e9;
                color: #2e7d32;
                border-radius: 11px;
                padding: 2px 14px;
                font-size: 12px;
                font-weight: bold;
            }
            #verdictPillNG {
                background: #ffebee;
                color: #c62828;
                border-radius: 11px;
                padding: 2px 14px;
                font-size: 12px;
                font-weight: bold;
            }

            /* ── Preview footer ──────────────────────────────────────── */
            #footerCaption {
                background: transparent;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                color: #7b8794;
            }
            #defectTypeValue {
                background: transparent;
                font-size: 14px;
                font-weight: bold;
                color: #c62828;
                padding-left: 8px;
            }

            /* Button palette (secondaryBtn/dangerBtn) — defined canonically in MainWindow QSS */

            /* Big primary CLOSE button */
            #closeBtn {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 10px;
                font-size: 17px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 12px 24px;
            }
            #closeBtn:hover  { background: #1976d2; }
            #closeBtn:pressed { background: #0d47a1; }

            /* Image preview */
            #previewPanel {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                padding: 4px;
            }
            #imagePreview {
                background: #1a1d23;
                border-radius: 4px;
                color: #a8b0ba;
                font-size: 13px;
            }

            /* Splitter */
            QSplitter::handle {
                background: #cbd1d9;
                width: 2px;
            }

            /* Scrollbars (bigger for touch) */
            QScrollBar:vertical {
                background: #eef0f3;
                width: 14px;
                border-radius: 7px;
            }
            QScrollBar::handle:vertical {
                background: #a8b0ba;
                border-radius: 7px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover { background: #52606d; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #eef0f3;
                height: 14px;
                border-radius: 7px;
            }
            QScrollBar::handle:horizontal {
                background: #a8b0ba;
                border-radius: 7px;
                min-width: 40px;
            }
            QScrollBar::handle:horizontal:hover { background: #52606d; }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal { width: 0; }

            /* Message/Confirm dialogs spawned from this viewer */
            QMessageBox {
                background: #ffffff;
                font-size: 14px;
            }
            QMessageBox QLabel {
                background: transparent;
                color: #1a1d23;
                font-size: 14px;
            }
            QMessageBox QPushButton {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                min-width: 100px;
                min-height: 38px;
                padding: 6px 18px;
            }
            QMessageBox QPushButton:hover  { background: #1976d2; }
            QMessageBox QPushButton:pressed { background: #0d47a1; }
        """)
