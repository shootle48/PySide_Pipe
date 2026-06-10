"""
batch_setup_dialog.py
─────────────────────
Custom dialog สำหรับเริ่ม batch ใหม่ — เลือกขนาด (L/M/S) + Target
+ (MA Mode) slider ปรับ min_defect_area per size

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
│  [MA Mode] ความไวการตรวจจับ defect:           │
│  เข้มงวด ◄════●══════════════════► หละหลวม   │
│                   750 px²  (~2.7%)  [Default]│
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

from PySide6.QtCore    import QLocale, QSettings, Qt, Signal
from PySide6.QtGui     import QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSpinBox, QVBoxLayout, QWidget,
)


# ลำดับ: ใหญ่ → กลาง → เล็ก  (1, 2, 3)
SIZE_OPTIONS = ("L", "M", "S")
SIZE_LABELS  = {"L": "Large", "M": "Medium", "S": "Small"}
SIZE_NUMBERS = {"L": "1",     "M": "2",      "S": "3"}

# Slider = "ระดับความเข้มงวด" 0–100% (UI)
#   ค่าที่ส่งหลังบ้าน = 100 − slider  (flip: Detection ตีความ ค่าน้อย=เข้มงวด)
#   - slider 0%   (เข้มงวดน้อย) → ส่ง 100 (หลวมสุด)
#   - slider 100% (เข้มงวดมาก) → ส่ง 0   (เข้มงวดสุด)
_THRESHOLD_DEFAULT = 100     # เริ่มที่ "เข้มงวดมาก" → ส่ง 0 (ค่าที่เอาไปคำนวณ)
_THRESHOLD_MIN     = 0
_THRESHOLD_MAX     = 100
_THRESHOLD_STEP    = 5

# QSettings key — เก็บ "ค่าที่ส่งหลังบ้าน" (0–100) ตรงๆ ให้ pipeline อ่านส่ง Detection
_SETTINGS_KEY_PCT = "detection/threshold_pct/{}"


def _load_threshold(size: str) -> int:
    """คืน 'ตำแหน่ง slider' (0–100) ของ size นี้

    QSettings เก็บค่าที่ส่งหลังบ้าน (100−slider) → แปลงกลับเป็นตำแหน่ง slider
    """
    s = QSettings()
    key = _SETTINGS_KEY_PCT.format(size)
    if s.contains(key):
        return 100 - int(float(s.value(key)))
    return _THRESHOLD_DEFAULT


def _save_threshold(size: str, slider_pos: int) -> None:
    """บันทึก threshold ลง QSettings

    เก็บ 'ค่าที่ส่งหลังบ้าน' = 100 − slider_pos (flip)
    """
    sent = 100 - int(slider_pos)
    s = QSettings()
    s.setValue(_SETTINGS_KEY_PCT.format(size), sent)
    s.sync()   # flush ให้ pipeline (QSettings instance แยก) อ่านเห็นทันที


def _reset_threshold(size: str) -> None:
    """ลบ override ออก → pipeline ส่ง None → Detection ใช้ default"""
    QSettings().remove(_SETTINGS_KEY_PCT.format(size))


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
    """Touch-friendly batch setup dialog (size + target + optional MA threshold)"""

    def __init__(
        self,
        parent: QWidget | None = None,
        default_size: str = "L",
        default_target: int = 0,
        threshold_mode: str = "off",   # "off" = ซ่อน slider / "on" = แสดง
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start New Batch")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._selected_size: str = default_size if default_size in SIZE_OPTIONS else "L"
        self._size_buttons: dict[str, _SizeButton] = {}
        self._threshold_mode = threshold_mode

        self._build_ui(default_target=default_target)
        self._apply_styles()
        self._highlight_size(self._selected_size)

        # โหลดค่า threshold เริ่มต้นสำหรับ size ที่เลือก
        if threshold_mode == "on":
            self._load_slider_for_size(self._selected_size)

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

        # ── Threshold slider (MA Mode) ─────────────────────────────────
        self._threshold_frame = QFrame()
        self._threshold_frame.setObjectName("thresholdFrame")
        th_layout = QVBoxLayout(self._threshold_frame)
        th_layout.setContentsMargins(0, 6, 0, 0)
        th_layout.setSpacing(8)

        th_title = QLabel("ความไวการตรวจจับ DEFECT  [MA MODE]")
        th_title.setObjectName("sectionLabel")
        th_layout.addWidget(th_title)

        # ค่า (ใหญ่ ซ้าย) + ปุ่ม DEFAULT (ขวา)
        val_row = QHBoxLayout()
        self._threshold_value_label = QLabel()
        self._threshold_value_label.setObjectName("thresholdValue")
        val_row.addWidget(self._threshold_value_label)
        val_row.addStretch()
        self._default_btn = QPushButton("DEFAULT")
        self._default_btn.setObjectName("defaultBtn")
        self._default_btn.setFixedHeight(32)
        self._default_btn.setCursor(Qt.PointingHandCursor)
        self._default_btn.clicked.connect(self._on_reset_threshold)
        val_row.addWidget(self._default_btn)
        th_layout.addLayout(val_row)

        # Slider
        self._threshold_slider = QSlider(Qt.Horizontal)
        self._threshold_slider.setRange(_THRESHOLD_MIN, _THRESHOLD_MAX)
        self._threshold_slider.setSingleStep(_THRESHOLD_STEP)
        self._threshold_slider.setPageStep(_THRESHOLD_STEP * 2)
        self._threshold_slider.setValue(_THRESHOLD_DEFAULT)
        self._threshold_slider.setFixedHeight(34)
        self._threshold_slider.valueChanged.connect(self._on_slider_changed)
        th_layout.addWidget(self._threshold_slider)

        # สเกล MIN / 50% / MAX (ใต้ slider)
        scale_row = QHBoxLayout()
        lbl_min = QLabel("MIN")
        lbl_min.setObjectName("dimLabel")
        lbl_mid = QLabel("50%")
        lbl_mid.setObjectName("dimLabel")
        lbl_mid.setAlignment(Qt.AlignCenter)
        lbl_max = QLabel("MAX")
        lbl_max.setObjectName("dimLabel")
        lbl_max.setAlignment(Qt.AlignRight)
        scale_row.addWidget(lbl_min)
        scale_row.addStretch()
        scale_row.addWidget(lbl_mid)
        scale_row.addStretch()
        scale_row.addWidget(lbl_max)
        th_layout.addLayout(scale_row)

        root.addWidget(self._threshold_frame)

        # ซ่อน threshold section ถ้า mode="off"
        self._threshold_frame.setVisible(self._threshold_mode == "on")

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
        self._target_spin.setButtonSymbols(QSpinBox.NoButtons)   # เอาลูกศรขึ้น/ลงออก
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
        self._ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._ok_btn)

        root.addLayout(btn_row)

        # อัปเดต label ค่าเริ่มต้น
        self._update_value_label(_THRESHOLD_DEFAULT)

    # ── Threshold helpers ──────────────────────────────────────────────────

    def _load_slider_for_size(self, size: str) -> None:
        """โหลดค่า QSettings ของ size นี้แล้วตั้ง slider"""
        val = _load_threshold(size)
        self._threshold_slider.blockSignals(True)
        self._threshold_slider.setValue(val)
        self._threshold_slider.blockSignals(False)
        self._update_value_label(val)

    def _update_value_label(self, slider_pos: int) -> None:
        """อัปเดต label แสดงระดับความเข้มงวด + ค่าที่ส่งหลังบ้าน (ช่วย MA debug)"""
        self._threshold_value_label.setText(
            f"ความเข้มงวด {slider_pos}% "
        )

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_size_clicked(self, size: str) -> None:
        self._selected_size = size
        self._highlight_size(size)
        # อัปเดต slider เป็นค่าของ size ที่เลือก
        if self._threshold_mode == "on":
            self._load_slider_for_size(size)

    def _on_slider_changed(self, value: int) -> None:
        # snap ให้เป็น step
        snapped = round(value / _THRESHOLD_STEP) * _THRESHOLD_STEP
        snapped = max(_THRESHOLD_MIN, min(_THRESHOLD_MAX, snapped))
        if snapped != value:
            self._threshold_slider.blockSignals(True)
            self._threshold_slider.setValue(snapped)
            self._threshold_slider.blockSignals(False)
        self._update_value_label(snapped)

    def _on_reset_threshold(self) -> None:
        """รีเซ็ต slider กลับ default (เข้มงวดมาก 100% → ส่ง 0) — บันทึกตอน START"""
        self._threshold_slider.setValue(_THRESHOLD_DEFAULT)
        self._update_value_label(_THRESHOLD_DEFAULT)

    def _on_accept(self) -> None:
        """บันทึก threshold ลง QSettings แล้ว accept"""
        if self._threshold_mode == "on":
            _save_threshold(self._selected_size, self._threshold_slider.value())
        self.accept()

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
            /* แก้กล่องขาว: label/slider รับพื้น global ของ main window → บังคับโปร่งใส */
            QLabel  { background: transparent; }
            QSlider { background: transparent; }

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
            #dimLabel {
                font-size: 11px;
                color: #9aa5b1;
                font-weight: bold;
                letter-spacing: 1px;
            }
            /* ── Size button (QFrame) — ตัวเลข + label (แบบเดิม) ── */
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
            /* ── Threshold ── */
            #thresholdFrame {
                background: transparent;
                border: none;
            }
            #thresholdValue {
                font-size: 20px;
                font-weight: bold;
                color: #1a1d23;
            }
            #defaultBtn {
                background: #ffffff;
                color: #52606d;
                border: 1px solid #cbd1d9;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 4px 14px;
            }
            #defaultBtn:hover { background: #f1f3f6; border-color: #1565c0; color: #1565c0; }
            /* ── Slider (ธีมน้ำเงินเดิม) ── */
            QSlider::groove:horizontal {
                height: 6px;
                background: #dde1e7;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 22px;
                height: 22px;
                margin: -8px 0;
                border-radius: 11px;
                background: #1565c0;
                border: 2px solid #0d47a1;
            }
            QSlider::handle:horizontal:hover { background: #1976d2; }
            QSlider::sub-page:horizontal {
                background: #1565c0;
                border-radius: 3px;
            }
            /* ── Target spin ── */
            #targetSpin {
                background: #ffffff;
                border: 2px solid #cbd1d9;
                border-radius: 6px;
                font-size: 22px;
                font-weight: bold;
                padding: 4px 8px;
            }
            /* ── Buttons ── */
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
    threshold_mode: str = "off",
) -> tuple[str, int] | None:
    """
    เปิด BatchSetupDialog แล้ว return tuple (size, target) ถ้า user กด START
    หรือ None ถ้ากด CANCEL.

    threshold ถูกบันทึกลง QSettings อัตโนมัติเมื่อกด START (ถ้า mode="on")

    ตัวอย่าง:
        result = request_batch_setup(self, default_size="M", default_target=50)
        if result is None:
            return  # cancel
        size, target = result
        self._batch_state.reset(expected_total=target, expected_size=size)
    """
    dlg = BatchSetupDialog(
        parent=parent,
        default_size=default_size,
        default_target=default_target,
        threshold_mode=threshold_mode,
    )
    if dlg.exec() != QDialog.Accepted:
        return None
    return dlg.selected_size, dlg.selected_target
