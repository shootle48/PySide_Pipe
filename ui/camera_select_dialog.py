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
from typing import Optional

import cv2
from PySide6.QtCore    import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

logger = logging.getLogger(__name__)

MAX_SCAN_INDEX = 6   # สแกน index 0..5 (ส่วนใหญ่กล้อง USB ไม่เกินนี้)


def scan_cameras(skip: Optional[set] = None) -> list[dict]:
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

    def __init__(self, current_index: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Camera")
        self.setMinimumWidth(400)
        self._current_index  = current_index
        self._selected_index: Optional[int] = None

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

        # Apply stylesheet ให้เข้ากับ main window
        self.setStyleSheet("""
            QDialog { background: #141720; color: #e8eaf0; }
            QLabel  { color: #e8eaf0; font-size: 12px; }
            QListWidget {
                background: #0d0f14;
                border: 1px solid #2a2f45;
                border-radius: 4px;
                color: #e8eaf0;
                font-family: "Consolas", monospace;
                font-size: 12px;
            }
            QListWidget::item { padding: 6px 4px; }
            QListWidget::item:selected { background: #1c2a3a; color: #60b4ff; }
            QPushButton {
                background: #1c1f2e;
                color: #e8eaf0;
                border: 1px solid #2a2f45;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover { background: #2a2f45; border-color: #3a4060; }
            QPushButton:default { background: #1a3a5c; color: #60b4ff; border-color: #2a5a8c; }
        """)

        # Initial scan
        self._refresh()

    def _refresh(self) -> None:
        """Re-scan กล้องทั้งหมด (ยกเว้น current ที่ worker ใช้อยู่)"""
        self._list.clear()
        self._info_label.setText("กำลังสแกน…")
        self._refresh_btn.setEnabled(False)
        QApplication.processEvents()

        # Skip current เพื่อไม่ให้ conflict กับ worker ที่กำลังเปิดอยู่
        cams = scan_cameras(skip={self._current_index})

        # เพิ่ม current เข้า list (แสดงเป็น active)
        current_item = QListWidgetItem(
            f"  ● Camera {self._current_index}  —  active (currently in use)"
        )
        current_item.setData(Qt.UserRole, self._current_index)
        current_item.setForeground(Qt.GlobalColor.green)
        self._list.addItem(current_item)
        self._list.setCurrentItem(current_item)

        # เพิ่มกล้องอื่นที่เจอ
        for cam in cams:
            text = (
                f"    Camera {cam['index']}  —  "
                f"{cam['width']}×{cam['height']} @ {cam['fps']:.0f}fps"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cam["index"])
            self._list.addItem(item)

        total = len(cams) + 1
        self._info_label.setText(f"พบ {total} กล้อง — ดับเบิลคลิกหรือกด Select")
        self._refresh_btn.setEnabled(True)

    def _on_select(self) -> None:
        item = self._list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Select Camera", "กรุณาเลือกกล้อง 1 รายการ")
            return
        self._selected_index = item.data(Qt.UserRole)
        self.accept()

    def selected_index(self) -> Optional[int]:
        return self._selected_index
