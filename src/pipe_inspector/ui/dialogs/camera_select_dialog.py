"""
camera_select_dialog.py
───────────────────────
Dialog สำหรับ scan กล้องที่มีอยู่ในระบบ และให้ user เลือก
คืนค่า camera index ที่เลือก (หรือ None ถ้ากด Cancel)

ใช้งาน:
    dialog = CameraSelectDialog(current_index=0, parent=self)
    if dialog.exec() == QDialog.Accepted:
        new_idx = dialog.selected_index()
"""

from __future__ import annotations

import logging

import cv2
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

MAX_SCAN_INDEX = 6   # สแกน index 0..5 (ส่วนใหญ่กล้อง USB ไม่เกินนี้)


class _ScanWorker(QThread):
    """รัน scan_cameras() บน background thread แล้วส่งผลกลับผ่าน signal."""
    found = Signal(list)  # list[dict]

    def __init__(self, skip: set) -> None:
        super().__init__()
        self._skip = skip

    def run(self) -> None:
        self.found.emit(scan_cameras(skip=self._skip))


def scan_cameras(skip: set | None = None) -> list[dict]:
    """
    สแกนกล้องใน index 0..MAX_SCAN_INDEX-1 → คืน list ของ dict
    {"index": int, "width": int, "height": int, "fps": float}

    Args:
        skip: set ของ index ที่จะข้าม (เช่น index ที่ worker กำลังใช้อยู่)
    """
    skip = skip or set()
    found = []
    for idx in range(MAX_SCAN_INDEX):
        if idx in skip:
            continue
        cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            found.append({"index": idx, "width": w, "height": h, "fps": fps})
            logger.info(f"scan_cameras: found Camera {idx} ({w}x{h} @ {fps:.0f}fps)")
        cap.release()
    return found


class CameraSelectDialog(QDialog):
    """Dialog เลือกกล้อง — scan, list, pick."""

    def __init__(
        self,
        current_index: int,
        camera_ok: bool = True,   # False = กล้องปัจจุบัน fail → auto-select ตัวแรกที่เจอ
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Camera")
        self.setMinimumWidth(400)
        self._current_index  = current_index
        self._camera_ok      = camera_ok
        self._selected_index: int | None = None
        self._scan_worker: _ScanWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Info / status label
        self._info_label = QLabel("กำลังสแกนกล้อง…")
        self._info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._info_label)

        # Camera list
        self._list = QListWidget()
        self._list.setMinimumHeight(180)
        self._list.itemDoubleClicked.connect(lambda _: self._on_select())
        layout.addWidget(self._list)

        # Buttons
        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("🔄  Refresh")
        self._refresh_btn.clicked.connect(self._refresh)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        self._ok_btn = QPushButton("✓  Select")
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_select)

        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._ok_btn)
        layout.addLayout(btn_row)

        # Light industrial HMI theme — ตรงกับ main_window.py palette
        self.setStyleSheet("""
            QDialog {
                background: #eef0f3;
                color: #1a1d23;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 14px;
            }
            QLabel { color: #1a1d23; font-size: 13px; }
            QListWidget {
                background: #ffffff;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                color: #1a1d23;
                font-family: "Consolas", monospace;
                font-size: 13px;
                padding: 2px;
            }
            QListWidget::item { padding: 10px 8px; border-bottom: 1px solid #eef0f3; }
            QListWidget::item:selected {
                background: #e3f2fd;
                border-left: 4px solid #1565c0;
                color: #0d47a1;
            }
            QListWidget::item:hover:!selected { background: #f1f3f6; }
            QPushButton {
                background: #ffffff;
                color: #1a1d23;
                border: 2px solid #a8b0ba;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { background: #e3f2fd; border-color: #1565c0; color: #0d47a1; }
            QPushButton:pressed { background: #bbdefb; }
            QPushButton:default {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
            }
            QPushButton:default:hover { background: #1976d2; }
            QPushButton:default:pressed { background: #0d47a1; }
        """)

        # Initial scan
        self._refresh()

    def _refresh(self) -> None:
        """Re-scan กล้องทั้งหมด (background thread — UI ไม่ freeze)"""
        if self._scan_worker and self._scan_worker.isRunning():
            return  # scan กำลังทำงานอยู่แล้ว

        self._list.clear()
        self._info_label.setText("กำลังสแกน…")
        self._refresh_btn.setEnabled(False)
        self._ok_btn.setEnabled(False)

        self._scan_worker = _ScanWorker(skip={self._current_index})
        self._scan_worker.found.connect(self._on_scan_done)
        # finished ยิงเสมอแม้ run() throw → กันปุ่มค้าง disabled (soft-lock ปิด dialog ไม่ได้)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_scan_finished(self) -> None:
        """re-enable ปุ่มเสมอเมื่อ scan thread จบ (ครอบกรณี scan_cameras crash)"""
        self._refresh_btn.setEnabled(True)
        self._ok_btn.setEnabled(True)

    def _on_scan_done(self, cams: list) -> None:
        """รับผลจาก background scan — เรียกบน main thread ผ่าน signal"""
        # แถวแรก: กล้องปัจจุบัน (active หรือ failed)
        if self._camera_ok:
            current_label = f"  ● Camera {self._current_index}  —  active (currently in use)"
            current_color = Qt.GlobalColor.green
        else:
            current_label = f"  ✕ Camera {self._current_index}  —  failed (ใช้งานไม่ได้)"
            current_color = Qt.GlobalColor.red

        current_item = QListWidgetItem(current_label)
        current_item.setData(Qt.UserRole, self._current_index)
        current_item.setForeground(current_color)
        self._list.addItem(current_item)

        # เพิ่มกล้องอื่นที่เจอ
        first_ok_item = None
        for cam in cams:
            text = (
                f"    Camera {cam['index']}  —  "
                f"{cam['width']}×{cam['height']} @ {cam['fps']:.0f}fps"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cam["index"])
            self._list.addItem(item)
            if first_ok_item is None:
                first_ok_item = item

        # ถ้ากล้องปัจจุบัน fail → auto-select กล้องตัวแรกที่ใช้ได้
        if not self._camera_ok and first_ok_item is not None:
            self._list.setCurrentItem(first_ok_item)
            self._info_label.setText(
                f"กล้อง {self._current_index} ใช้ไม่ได้ — เลือก Camera {first_ok_item.data(Qt.UserRole)} แทนอัตโนมัติ"
            )
        else:
            self._list.setCurrentItem(current_item)
            total = len(cams) + 1
            self._info_label.setText(f"พบ {total} กล้อง — ดับเบิลคลิกหรือกด Select")

        self._refresh_btn.setEnabled(True)
        self._ok_btn.setEnabled(True)

    def _on_select(self) -> None:
        item = self._list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Select Camera", "กรุณาเลือกกล้อง 1 รายการ")
            return
        self._selected_index = item.data(Qt.UserRole)
        self.accept()

    def selected_index(self) -> int | None:
        return self._selected_index
