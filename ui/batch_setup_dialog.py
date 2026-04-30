"""
batch_setup_dialog.py
─────────────────────
Custom dialog สำหรับเริ่ม batch ใหม่ — เลือกขนาด (S/M/L) + Target

แทน QInputDialog.getInt() แบบเดิมที่ถาม Target อย่างเดียว
ใช้ใน main_window._reset_batch()

Layout (touch-friendly สำหรับ factory):
┌──────────────────────────────────────────────┐
│  START NEW BATCH                             │
│                                              │
│  เลือกขนาดชิ้นงานใน batch นี้:                  │
│  ┌──────┐  ┌──────┐  ┌──────┐                │
│  │  S   │  │  M   │  │  L   │                │
│  └──────┘  └──────┘  └──────┘                │
│                                              │
│  จำนวนเป้าหมาย (Target):                       │
│  [           50          ] ↕                  │
│                                              │
│       [ CANCEL ]    [   START   ]            │
└──────────────────────────────────────────────┘

Returns:
  exec() → QDialog.Accepted หรือ QDialog.Rejected
  ค่าผ่าน properties:
    .selected_size   → "S" / "M" / "L"
    .selected_target → int
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore    import QLocale, Qt
from PySide6.QtGui     import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)


SIZE_OPTIONS = ("S", "M", "L")


class BatchSetupDialog(QDialog):
    """Touch-friendly batch setup dialog (size + target)"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        default_size: str = "M",
        default_target: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start New Batch")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._selected_size: str = default_size if default_size in SIZE_OPTIONS else "M"
        self._size_buttons: dict[str, QPushButton] = {}

        self._build_ui(default_target=default_target)
        self._apply_styles()
        self._highlight_size(self._selected_size)

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def selected_size(self) -> str:
        return self._selected_size

    @property
    def selected_target(self) -> int:
        return self._target_spin.value()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self, default_target: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Title
        title = QLabel("START NEW BATCH")
        title.setObjectName("dialogTitle")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # Size selector
        size_label = QLabel("เลือกขนาดชิ้นงานใน batch นี้")
        size_label.setObjectName("sectionLabel")
        root.addWidget(size_label)

        size_row = QHBoxLayout()
        size_row.setSpacing(12)
        for size in SIZE_OPTIONS:
            btn = QPushButton(size)
            btn.setObjectName("sizeBtn")
            btn.setCheckable(True)
            btn.setFixedHeight(72)
            btn.setMinimumWidth(110)
            btn.clicked.connect(lambda checked=False, s=size: self._on_size_clicked(s))
            self._size_buttons[size] = btn
            size_row.addWidget(btn)
        root.addLayout(size_row)

        # Target input
        target_label = QLabel("จำนวนเป้าหมาย (Target)  —  0 = ไม่ระบุ")
        target_label.setObjectName("sectionLabel")
        root.addWidget(target_label)

        self._target_spin = QSpinBox()
        self._target_spin.setRange(0, 1_000_000)
        self._target_spin.setValue(default_target)
        self._target_spin.setFixedHeight(50)
        self._target_spin.setObjectName("targetSpin")
        self._target_spin.setAlignment(Qt.AlignCenter)
        # Force Arabic numerals (0-9) — ป้องกันเลขไทย (๐-๙) บน locale ภาษาไทย
        self._target_spin.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        root.addWidget(self._target_spin)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._cancel_btn = QPushButton("CANCEL")
        self._cancel_btn.setObjectName("cancelBtn")
        self._cancel_btn.setFixedHeight(56)
        self._cancel_btn.setMinimumWidth(140)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addStretch()

        self._ok_btn = QPushButton("START")
        self._ok_btn.setObjectName("okBtn")
        self._ok_btn.setFixedHeight(56)
        self._ok_btn.setMinimumWidth(160)
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._ok_btn)

        root.addLayout(btn_row)

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_size_clicked(self, size: str) -> None:
        self._selected_size = size
        self._highlight_size(size)

    def _highlight_size(self, size: str) -> None:
        """อัพ visual state ให้ปุ่มขนาดที่เลือกเป็น active"""
        for s, btn in self._size_buttons.items():
            btn.setChecked(s == size)

    # ── Stylesheet ────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background: #eef0f3;
                font-family: "Segoe UI", system-ui, sans-serif;
                color: #1a1d23;
            }
            #dialogTitle {
                font-size: 18px;
                font-weight: bold;
                letter-spacing: 2px;
                color: #1a1d23;
                padding-bottom: 4px;
            }
            #sectionLabel {
                font-size: 13px;
                color: #52606d;
                font-weight: bold;
            }
            #sizeBtn {
                background: #ffffff;
                color: #1a1d23;
                border: 2px solid #cbd1d9;
                border-radius: 8px;
                font-size: 28px;
                font-weight: bold;
            }
            #sizeBtn:hover {
                background: #f1f3f6;
                border-color: #1565c0;
            }
            #sizeBtn:checked {
                background: #1565c0;
                color: #ffffff;
                border-color: #0d47a1;
            }
            #targetSpin {
                background: #ffffff;
                border: 2px solid #cbd1d9;
                border-radius: 6px;
                font-size: 22px;
                font-weight: bold;
                padding: 4px 8px;
            }
            #okBtn {
                background: #1565c0;
                color: #ffffff;
                border: 2px solid #0d47a1;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
                letter-spacing: 2px;
            }
            #okBtn:hover  { background: #1976d2; }
            #okBtn:pressed { background: #0d47a1; }
            #cancelBtn {
                background: #ffffff;
                color: #52606d;
                border: 2px solid #cbd1d9;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 2px;
            }
            #cancelBtn:hover { background: #f1f3f6; }
        """)


# ═══════════════════════════════════════════════════════════════════════════
# Convenience function — เรียกแบบครั้งเดียว
# ═══════════════════════════════════════════════════════════════════════════

def request_batch_setup(
    parent: Optional[QWidget] = None,
    default_size: str = "M",
    default_target: int = 0,
) -> Optional[tuple[str, int]]:
    """
    เปิด BatchSetupDialog แล้ว return tuple (size, target) ถ้า user กด START
    หรือ None ถ้ากด CANCEL.

    ตัวอย่าง:
        result = request_batch_setup(self, default_size="M", default_target=50)
        if result is None:
            return  # cancel
        size, target = result
        self._batch_state.reset(expected_total=target, expected_size=size)
    """
    dlg = BatchSetupDialog(parent=parent, default_size=default_size, default_target=default_target)
    if dlg.exec() != QDialog.Accepted:
        return None
    return dlg.selected_size, dlg.selected_target
