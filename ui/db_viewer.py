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
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore    import Qt, QThread, Signal, Slot
from PySide6.QtGui     import QColor, QFont, QImage, QPixmap
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
        self._row_images:    list[str] = []   # image_b64 per table row (index-aligned)

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

        delete_batch_btn = QPushButton("🗑  ลบ Batch นี้")
        delete_batch_btn.setObjectName("dangerBtn")
        delete_batch_btn.setFixedHeight(28)
        delete_batch_btn.clicked.connect(self._delete_current_batch)
        layout.addWidget(delete_batch_btn)

        return panel

    # ── Right: inspection table + image preview ─────────────────────────

    def _build_inspection_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel("INSPECTIONS")
        label.setObjectName("sectionTitle")
        layout.addWidget(label)

        # Vertical splitter: table (top) / image preview (bottom)
        v_split = QSplitter(Qt.Vertical)
        v_split.setHandleWidth(1)
        v_split.setChildrenCollapsible(False)

        # ── Table ──────────────────────────────────────────────────────
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
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        v_split.addWidget(self._table)

        # ── Image preview ───────────────────────────────────────────────
        preview_panel = QWidget()
        preview_panel.setObjectName("previewPanel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 6, 0, 0)
        preview_layout.setSpacing(4)

        title_row = QHBoxLayout()
        preview_title = QLabel("NG IMAGE PREVIEW")
        preview_title.setObjectName("sectionTitle")
        title_row.addWidget(preview_title)
        title_row.addStretch()
        self._fullscreen_btn = QPushButton("⛶  ขยาย")
        self._fullscreen_btn.setObjectName("secondaryBtn")
        self._fullscreen_btn.setFixedHeight(22)
        self._fullscreen_btn.setEnabled(False)
        self._fullscreen_btn.clicked.connect(self._open_fullscreen)
        title_row.addWidget(self._fullscreen_btn)
        preview_layout.addLayout(title_row)

        self._preview_label = QLabel()
        self._preview_label.setObjectName("imagePreview")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setText("— เลือก row ที่เป็น NG เพื่อดูภาพ —")
        self._preview_label.setMinimumHeight(160)
        preview_layout.addWidget(self._preview_label, stretch=1)

        self._current_pixmap: Optional[QPixmap] = None

        v_split.addWidget(preview_panel)
        v_split.setSizes([340, 200])
        layout.addWidget(v_split, stretch=1)

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

        clear_img_btn = QPushButton("🖼  Clear Image")
        clear_img_btn.setObjectName("secondaryBtn")
        clear_img_btn.setToolTip("เคลียร์รูปของ record ที่เลือก (เก็บ metadata ไว้)")
        clear_img_btn.clicked.connect(self._clear_selected_image)
        bottom.addWidget(clear_img_btn)

        delete_row_btn = QPushButton("🗑  ลบ Record")
        delete_row_btn.setObjectName("dangerBtn")
        delete_row_btn.setToolTip("ลบ inspection record ที่เลือกออกทั้งหมด")
        delete_row_btn.clicked.connect(self._delete_selected_record)
        bottom.addWidget(delete_row_btn)

        export_btn = QPushButton("📄  Export CSV")
        export_btn.setObjectName("secondaryBtn")
        export_btn.clicked.connect(self._export_csv)
        bottom.addWidget(export_btn)

        export_ds_btn = QPushButton("📦  Export Dataset")
        export_ds_btn.setObjectName("secondaryBtn")
        export_ds_btn.setToolTip("Export รูป + annotations สำหรับทำ dataset train model")
        export_ds_btn.clicked.connect(self._export_dataset)
        bottom.addWidget(export_ds_btn)

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
        self._row_images = []   # reset parallel image list
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
            det_text = ", ".join(d.get("label", "unknown") for d in dets) if dets else "—"
            self._set_cell(row_idx, 3, det_text)

            # Timestamp (local time)
            ts = row_data["timestamp"]
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            self._set_cell(row_idx, 4, ts)

            # Store image_b64 parallel to row index
            self._row_images.append(row_data.get("image_b64") or "")

        # Update stats bar
        self._stat_total.setText(f"Total: {total}")
        self._stat_ng.setText(f"NG: {ng}")
        rate = f"{ng/total*100:.1f}%" if total > 0 else "—"
        self._stat_rate.setText(f"NG Rate: {rate}")

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFont(QFont("Consolas", 10))
        self._table.setItem(row, col, item)

    def _update_stats_bar(self, total: int, ng: int) -> None:
        """อัพเดต stats bar ด้านล่างตาราง และ batch list item ซ้าย."""
        # Stats bar
        self._stat_total.setText(f"Total: {total}")
        self._stat_ng.setText(f"NG: {ng}")
        rate = f"{ng/total*100:.1f}%" if total > 0 else "—"
        self._stat_rate.setText(f"NG Rate: {rate}")

        # Batch list item (อัพ text ตรงๆ ไม่ reload ทั้งหมด)
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
        """แสดง NG image เมื่อ user คลิก row ในตาราง"""
        selected = self._table.selectedItems()
        if not selected:
            return
        row_idx = self._table.row(selected[0])
        if row_idx >= len(self._row_images):
            return

        b64 = self._row_images[row_idx]
        if not b64:
            self._current_pixmap = None
            self._fullscreen_btn.setEnabled(False)
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("— ไม่มีภาพ (OK result หรือบันทึกก่อนอัปเดต) —")
            return

        try:
            import base64 as _b64
            img_bytes = _b64.b64decode(b64)
            qimage    = QImage.fromData(img_bytes)
            if qimage.isNull():
                raise ValueError("QImage decode returned null")
            self._current_pixmap = QPixmap.fromImage(qimage)
        except Exception as exc:
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
        """เปิดภาพเต็มจอใน QDialog แยก"""
        if self._current_pixmap is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("NG Image — Fullscreen")
        dlg.showMaximized()

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setStyleSheet("background: #0d0f14;")
        screen = dlg.screen().availableGeometry()
        img_label.setPixmap(
            self._current_pixmap.scaled(
                screen.width(), screen.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        layout.addWidget(img_label)

        close_btn = QPushButton("✕  ปิด  (หรือกด Esc)")
        close_btn.setObjectName("secondaryBtn")
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(dlg.close)
        layout.addWidget(close_btn)

        dlg.exec()

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

    def _get_selected_piece_id(self) -> Optional[str]:
        """Return piece_id ของ row ที่เลือกใน table หรือ None"""
        selected = self._table.selectedItems()
        if not selected:
            return None
        return self._table.item(self._table.row(selected[0]), 0).text()

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
        # อัปเดต local cache + preview
        row_idx = self._table.row(self._table.selectedItems()[0])
        if row_idx < len(self._row_images):
            self._row_images[row_idx] = ""
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
        row_idx = self._table.row(self._table.selectedItems()[0])
        self._table.removeRow(row_idx)
        if row_idx < len(self._row_images):
            self._row_images.pop(row_idx)
        self._table.clearSelection()   # ป้องกัน index out of sync

        # Sync counters: recalc จาก DB จริง แล้วอัพ stats bar + batch list item
        if self._current_batch:
            total, ng = self._db.recalculate_batch_counters(self._current_batch)
            self._update_stats_bar(total, ng)
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText("— เลือก row ที่เป็น NG เพื่อดูภาพ —")
        self._current_pixmap = None
        self._fullscreen_btn.setEnabled(False)

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
        self._row_images.clear()
        self._stat_total.setText("Total: 0")
        self._stat_ng.setText("NG: 0")
        self._stat_rate.setText("NG Rate: —")
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

    @Slot()
    def _export_dataset(self) -> None:
        """Export NG images + annotations เป็น dataset พร้อม train model.

        Structure:
          dataset_export_YYYYMMDD_HHMMSS/
          ├── NG/
          │   ├── BATCH-XXXXXX-0001.jpg
          │   └── ...
          └── annotations.csv  (piece_id, batch_id, verdict, confidence,
                               label, bbox_x, bbox_y, bbox_w, bbox_h, timestamp)
        """
        import base64
        from pathlib import Path

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
        except Exception as exc:
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
            except Exception as exc:
                logger.error(f"Failed to save {piece_id}: {exc}")
                failed += 1
                continue

            # Annotations — 1 row per inspection (รวม label หลายตัวด้วย comma)
            dets   = r.get("detections", [])
            labels = ", ".join(d.get("label", "unknown") for d in dets) if dets else ""
            total, ng = batch_counters.get(r["batch_id"], (0, 0))
            csv_rows.append([
                piece_id, r["batch_id"], r["verdict"], f"{r['confidence']:.3f}",
                labels, total, ng, r["timestamp"],
            ])

        # Write annotations.csv
        try:
            with open(out_dir / "annotations.csv", "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "piece_id", "batch_id", "verdict", "confidence",
                    "label", "total", "ng", "timestamp",
                ])
                writer.writerows(csv_rows)
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"เขียน annotations.csv ไม่สำเร็จ:\n{exc}")
            return

        msg = (
            f"Saved to:\n{out_dir}\n\n"
            f"✅ Images saved: {saved_images}\n"
            f"📝 Annotations: {len(csv_rows)} rows\n"
        )
        if failed:
            msg += f"⚠ Failed: {failed} images\n"
        QMessageBox.information(self, "Export Dataset Complete", msg)

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

            /* Danger button */
            #dangerBtn {
                background: transparent;
                color: #ff1744;
                border: 1px solid #ff174466;
                border-radius: 4px;
                font-size: 12px;
                padding: 5px 12px;
            }
            #dangerBtn:hover {
                background: #ff174422;
                border-color: #ff1744;
            }

            /* Image preview */
            #previewPanel {
                background: #141720;
                border: 1px solid #2a2f45;
                border-radius: 6px;
                padding: 4px;
            }
            #imagePreview {
                background: #0d0f14;
                border-radius: 4px;
                color: #4a5070;
                font-size: 11px;
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
