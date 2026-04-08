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

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore    import Qt, QThread, Signal, Slot
from PySide6.QtGui     import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QFrame,
)

logger = logging.getLogger(__name__)


class DbViewerDialog(QDialog):
    """
    Database viewer dialog — shows all batches and their inspections.

    Usage:
        dialog = DbViewerDialog(db=db_manager, parent=main_window)
        dialog.exec()
    """

    def __init__(self, db, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db             = db
        self._current_batch  = None   # currently selected batch id

        self.setWindowTitle("Database Viewer")
        self.resize(1100, 660)
        self.setMinimumSize(800, 500)

        self._build_ui()
        self._apply_stylesheet()
        self._load_batches()

    # ══════════════════════════════════════════════════════════════════════
    # UI construction
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Header bar ─────────────────────────────────────────────────
        header = QHBoxLayout()

        title = QLabel("🗄   DATABASE VIEWER")
        title.setObjectName("dialogTitle")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.setFixedWidth(110)
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)

        root.addLayout(header)

        # ── Divider ────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("divider")
        root.addWidget(line)

        # ── Main splitter (batches left / inspections right) ───────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_batch_panel())
        splitter.addWidget(self._build_inspection_panel())
        splitter.setSizes([280, 820])

        root.addWidget(splitter, stretch=1)

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

        return panel

    # ── Right: inspection table ─────────────────────────────────────────

    def _build_inspection_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel("INSPECTIONS")
        label.setObjectName("sectionTitle")
        layout.addWidget(label)

        # Table
        self._table = QTableWidget()
        self._table.setObjectName("inspectionTable")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "Piece ID", "Verdict", "Confidence", "Defects", "Timestamp",
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(0, 180)
        self._table.setColumnWidth(1, 80)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 120)
        layout.addWidget(self._table, stretch=1)

        # Stats + export bar
        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        self._stat_total = QLabel("Total: —")
        self._stat_total.setObjectName("statLabel")
        self._stat_ng    = QLabel("NG: —")
        self._stat_ng.setObjectName("statLabel")
        self._stat_rate  = QLabel("NG Rate: —")
        self._stat_rate.setObjectName("statLabel")

        bottom.addWidget(self._stat_total)
        bottom.addWidget(self._stat_ng)
        bottom.addWidget(self._stat_rate)
        bottom.addStretch()

        export_btn = QPushButton("📄  Export CSV")
        export_btn.setObjectName("secondaryBtn")
        export_btn.clicked.connect(self._export_csv)
        bottom.addWidget(export_btn)

        layout.addLayout(bottom)
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
            started = b["started_at"][:16].replace("T", " ")
            text = (
                f"{b['id']}{active_mark}\n"
                f"  {started}   Total: {b['total']}   NG: {b['ng']}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, b["id"])
            item.setFont(QFont("Consolas", 10))
            if b["is_active"]:
                item.setForeground(QColor("#29b6f6"))
            self._batch_list.addItem(item)

        if self._batch_list.count() > 0:
            self._batch_list.setCurrentRow(0)

    def _load_inspections(self, batch_id: str) -> None:
        """Load inspections for the selected batch into the table."""
        self._current_batch = batch_id
        rows = self._db.get_recent_inspections(batch_id, limit=500)

        self._table.setRowCount(0)
        total = len(rows)
        ng    = sum(1 for r in rows if r["verdict"] == "NG")

        for row_data in rows:
            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)

            # Piece ID
            self._set_cell(row_idx, 0, row_data["piece_id"])

            # Verdict (coloured)
            verdict_item = QTableWidgetItem(row_data["verdict"])
            verdict_item.setTextAlignment(Qt.AlignCenter)
            if row_data["verdict"] == "NG":
                verdict_item.setForeground(QColor("#ff1744"))
                verdict_item.setBackground(QColor("#1f1520"))
            else:
                verdict_item.setForeground(QColor("#00e676"))
            self._table.setItem(row_idx, 1, verdict_item)

            # Confidence
            conf_item = QTableWidgetItem(f"{row_data['confidence']:.1%}")
            conf_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row_idx, 2, conf_item)

            # Defects
            dets = row_data.get("detections", [])
            det_text = ", ".join(d["label"] for d in dets) if dets else "—"
            self._set_cell(row_idx, 3, det_text)

            # Timestamp (local time)
            ts = row_data["timestamp"]
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            self._set_cell(row_idx, 4, ts)

        # Update stats bar
        self._stat_total.setText(f"Total: {total}")
        self._stat_ng.setText(f"NG: {ng}")
        rate = f"{ng/total*100:.1f}%" if total > 0 else "—"
        self._stat_rate.setText(f"NG Rate: {rate}")

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFont(QFont("Consolas", 10))
        self._table.setItem(row, col, item)

    # ══════════════════════════════════════════════════════════════════════
    # Slots
    # ══════════════════════════════════════════════════════════════════════

    @Slot()
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
                writer.writerow(["piece_id", "verdict", "confidence", "detections", "timestamp"])
                for r in rows:
                    dets = ", ".join(d["label"] for d in r.get("detections", []))
                    writer.writerow([
                        r["piece_id"],
                        r["verdict"],
                        f"{r['confidence']:.3f}",
                        dets or "—",
                        r["timestamp"],
                    ])
            QMessageBox.information(self, "Export Complete", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    # ══════════════════════════════════════════════════════════════════════
    # Stylesheet
    # ══════════════════════════════════════════════════════════════════════

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QDialog, QWidget {
                background: #0d0f14;
                color: #e8eaf0;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 13px;
            }

            #dialogTitle {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #e8eaf0;
            }

            #divider {
                color: #2a2f45;
                background: #2a2f45;
                max-height: 1px;
            }

            #sectionTitle {
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #7a82a0;
            }

            /* Batch list */
            #batchList {
                background: #141720;
                border: 1px solid #2a2f45;
                border-radius: 6px;
                font-family: "Consolas", monospace;
                font-size: 11px;
            }
            #batchList::item {
                padding: 8px 10px;
                border-bottom: 1px solid #1c1f2e;
            }
            #batchList::item:selected {
                background: #1c2a3a;
                border-left: 3px solid #29b6f6;
            }
            #batchList::item:hover {
                background: #1c1f2e;
            }

            /* Inspection table */
            #inspectionTable {
                background: #141720;
                border: 1px solid #2a2f45;
                border-radius: 6px;
                gridline-color: #2a2f45;
                font-family: "Consolas", monospace;
                font-size: 11px;
                alternate-background-color: #1c1f2e;
            }
            #inspectionTable::item {
                padding: 4px 8px;
            }
            #inspectionTable::item:selected {
                background: #1c2a3a;
            }
            QHeaderView::section {
                background: #1c1f2e;
                color: #7a82a0;
                border: none;
                border-bottom: 1px solid #2a2f45;
                padding: 6px 8px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }

            /* Stats */
            #statLabel {
                font-family: "Consolas", monospace;
                font-size: 12px;
                color: #7a82a0;
            }

            /* Buttons */
            #secondaryBtn {
                background: transparent;
                color: #7a82a0;
                border: 1px solid #2a2f45;
                border-radius: 4px;
                font-size: 12px;
                padding: 5px 12px;
            }
            #secondaryBtn:hover {
                background: #1c1f2e;
                color: #e8eaf0;
                border-color: #3a4060;
            }

            /* Splitter */
            QSplitter::handle {
                background: #2a2f45;
                width: 1px;
            }

            /* Scrollbars */
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
            }
            QScrollBar::handle:vertical {
                background: #2a2f45;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: transparent;
                height: 6px;
            }
            QScrollBar::handle:horizontal {
                background: #2a2f45;
                border-radius: 3px;
                min-width: 20px;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal { width: 0; }
        """)
