"""
numpad.py
─────────
On-screen numeric keypad สำหรับจอ Touchscreen (ไม่มีคีย์บอร์ดจริงหน้างาน).

ขับ QSpinBox ที่ส่งเข้ามา — กดเลข 0-9 ต่อท้าย, C = ล้างเป็นค่าต่ำสุด, ⌫ = ลบหลักท้าย.
setValue() ของ QSpinBox clamp ช่วง [minimum, maximum] ให้อัตโนมัติ → ไม่ต้องเช็คเอง.

ใช้ร่วมกันได้ทั้ง BatchSetupDialog (Reset Batch) และ Set Target dialog:

    spin = QSpinBox(); spin.setRange(0, 1_000_000)
    layout.addWidget(spin)
    layout.addWidget(NumPad(spin))
"""

from __future__ import annotations

from PySide6.QtCore    import Qt
from PySide6.QtWidgets import QGridLayout, QPushButton, QSpinBox, QWidget


class NumPad(QWidget):
    """คีย์แพดตัวเลขบนจอ — ขับ QSpinBox ที่ส่งเข้ามา."""

    def __init__(self, spin: QSpinBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spin = spin

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)

        #  1 2 3
        #  4 5 6
        #  7 8 9
        #  C 0 ⌫
        layout = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("C", 3, 0), ("0", 3, 1), ("⌫", 3, 2),
        ]
        for text, row, col in layout:
            btn = QPushButton(text)
            btn.setFixedHeight(58)
            btn.setFocusPolicy(Qt.NoFocus)          # ไม่แย่ง focus / ไม่เด้ง virtual keyboard
            btn.setCursor(Qt.PointingHandCursor)
            if text == "C":
                btn.setObjectName("numpadClear")
            elif text == "⌫":
                btn.setObjectName("numpadBack")
            else:
                btn.setObjectName("numpadKey")
            btn.clicked.connect(lambda _=False, t=text: self._on_key(t))
            grid.addWidget(btn, row, col)

        self.setStyleSheet(self._QSS)

    # ── key handling ────────────────────────────────────────────────────────

    def _on_key(self, text: str) -> None:
        if text == "C":
            self._spin.setValue(self._spin.minimum())
        elif text == "⌫":
            self._spin.setValue(self._spin.value() // 10)      # clamp ที่ minimum() ให้เอง
        else:
            self._spin.setValue(self._spin.value() * 10 + int(text))   # clamp ที่ maximum() ให้เอง

    # ── style ─────────────────────────────────────────────────────────────────

    _QSS = """
        QPushButton#numpadKey, QPushButton#numpadClear, QPushButton#numpadBack {
            font-size: 22px;
            font-weight: bold;
            border-radius: 8px;
        }
        QPushButton#numpadKey {
            background: #ffffff;
            color: #1a1d23;
            border: 2px solid #cbd1d9;
        }
        QPushButton#numpadKey:hover   { background: #e3f2fd; border-color: #5c8ee0; }
        QPushButton#numpadKey:pressed { background: #bbdefb; }
        QPushButton#numpadClear {
            background: #ffffff;
            color: #ef6c00;
            border: 2px solid #ef6c00;
        }
        QPushButton#numpadClear:hover   { background: #fff3e0; }
        QPushButton#numpadClear:pressed { background: #ffe0b2; }
        QPushButton#numpadBack {
            background: #ffffff;
            color: #52606d;
            border: 2px solid #a8b0ba;
        }
        QPushButton#numpadBack:hover   { background: #f1f3f6; border-color: #52606d; }
        QPushButton#numpadBack:pressed { background: #e4e7eb; }
    """
