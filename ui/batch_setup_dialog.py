"""
batch_setup_dialog.py
─────────────────────
Custom dialog สำหรับเริ่ม batch ใหม่ — เลือกขนาด (L/M/S) + Target

แทน QInputDialog.getInt() แบบเดิมที่ถาม Target อย่างเดียว
ใช้ใน main_window._reset_batch()

Layout (touch-friendly สำหรับ factory):
┌──────────────────────────────────────────────┐
│  START NEW BATCH                             │
│                                              │
│  เลือกขนาดชิ้นงานใน batch นี้:                  │
│  ┌──────┐  ┌──────┐  ┌──────┐                │
│  │  1   │  │  2   │  │  3   │                │
│  │Large │  │Medium│  │Small │                │
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
    .selected_size   → "L" / "M" / "S"
    .selected_target → int
"""

from __future__ import annotations


from PySide6.QtCore    import QLocale, Qt, Signal
from PySide6.QtGui     import QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)


# ลำดับ: ใหญ่ → กลาง → เล็ก  (1, 2, 3)
SIZE_OPTIONS = ("L", "M", "S")
SIZE_LABELS  = {"L": "Large", "M": "Medium", "S": "Small"}
SIZE_NUMBERS = {"L": "1",     "M": "2",      "S": "3"}


class _SizeButton(QFrame):
    """ปุ่มเลือกขนาด — แสดงตัวเลขใหญ่ + คำอธิบายเล็กๆ ข้างล่าง"""

    clicked = Signal(str)   # emit size key เช่น "L"

    def __init__(self, size_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size_key = size_key
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("sizeBtn")
        self.setFixedHeight(80)
        self.setMinimumWidth(110)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(2)

        self._num_label = QLabel(SIZE_NUMBERS[size_key])
        self._num_label.setObjectName("sizeBtnNumber")
        self._num_label.setAlignment(Qt.AlignCenter)
        self._num_label.setAttribute(Qt.WA_TranslucentBackground)

        self._desc_label = QLabel(SIZE_LABELS[size_key])
        self._desc_label.setObjectName("sizeBtnLabel")
        self._desc_label.setAlignment(Qt.AlignCenter)
        self._desc_label.setAttribute(Qt.WA_TranslucentBackground)

        layout.addWidget(self._num_label)
        layout.addWidget(self._desc_label)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        # Qt QSS ไม่รองรับ descendant selector กับ property (:active #child)
        # → set inline style บน label โดยตรงแทน
        if active:
            self._num_label.setStyleSheet("background: transparent; color: #ffffff;")
            self._desc_label.setStyleSheet("background: transparent; color: #b3d4ff;")
        else:
            self._num_label.setStyleSheet("background: transparent; color: #1a1d23;")
            self._desc_label.setStyleSheet("background: transparent; color: #52606d;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._size_key)
        super().mousePressEvent(event)


class BatchSetupDialog(QDialog):
    """Touch-friendly batch setup dialog (size + target)"""

    def __init__(
        self,
        parent: QWidget | None = None,
        default_size: str = "L",
        default_target: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start New Batch")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._selected_size: str = default_size if default_size in SIZE_OPTIONS else "L"
        self._size_buttons: dict[str, _SizeButton] = {}

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
        for size in SIZE_OPTIONS:          # L, M, S → 1, 2, 3
            btn = _SizeButton(size)
            btn.clicked.connect(self._on_size_clicked)
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
            btn.set_active(s == size)

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
            /* ── Size button (QFrame) ── */
            #sizeBtn {
                background: #ffffff;
                border: 2px solid #cbd1d9;
                border-radius: 10px;
            }
            #sizeBtn:hover {
                background: #eef2ff;
                border: 2px solid #5c8ee0;
            }
            #sizeBtn[active="true"] {
                background: #1565c0;
                border: 2px solid #0d47a1;
            }
            #sizeBtn[active="true"]:hover {
                background: #1976d2;
                border: 2px solid #1565c0;
            }
            /* ตัวเลขใหญ่ — สีถูก set ผ่าน set_active() โดยตรง */
            #sizeBtnNumber {
                font-size: 30px;
                font-weight: bold;
                color: #1a1d23;
            }
            /* คำอธิบายเล็ก — สีถูก set ผ่าน set_active() โดยตรง */
            #sizeBtnLabel {
                font-size: 11px;
                font-weight: normal;
                color: #52606d;
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
    parent: QWidget | None = None,
    default_size: str = "M",
    default_target: int = 0,
) -> tuple[str, int] | None:
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
