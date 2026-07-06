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

from ui.numpad import NumPad


# ลำดับ: ใหญ่ → กลาง → เล็ก  (1, 2, 3)
SIZE_OPTIONS = ("L", "M", "S")
SIZE_LABELS  = {"L": "Large", "M": "Medium", "S": "Small"}
SIZE_NUMBERS = {"L": "1",     "M": "2",      "S": "3"}

_SLIDER_MIN  = 0
_SLIDER_MAX  = 100
_SLIDER_STEP = 5

# ── 4 หลอดปรับ (per size) ที่ส่งให้ Detection ──────────────────────────────
#   (suffix, label, flip, default_slider)
#   ทุกหลอด: slider = "ระดับความเข้มงวด" 0–100% ส่ง % ตรงๆ (ไม่ flip)
#   เพราะ Detection ตัวใหม่ตีความ "ค่ายิ่งมาก = ยิ่งเข้มงวด" ทั้งพื้นที่และแสง
#     - พื้นที่:  thresh = (1 − pct/100) × area  → pct สูง = thresh ต่ำ = จับ defect เยอะ
#     - แสง:     bright_thresh = 255 × 0.3922 × pct/100 → pct สูง = ยอมรับสีสว่างขึ้น = จับเยอะ
#   (flip ถูกถอดออก — ตัวเก่าตีความ ค่าน้อย=เข้มงวด จึงเคยต้อง flip, ตัวใหม่กลับด้าน)
_CH_OUTER_PCT       = "outer_pct"
_CH_INNER_PCT       = "inner_pct"
_CH_OUTER_LIGHT_PCT = "outer_light_pct"
_CH_INNER_LIGHT_PCT = "inner_light_pct"

#                  suffix              label                       flip   default(slider)
_THRESHOLD_CHANNELS = [
    (_CH_OUTER_PCT,       "พื้นที่ Defect — วงนอก",   False, 50),
    (_CH_INNER_PCT,       "พื้นที่ Defect — วงใน",    False, 50),
    (_CH_OUTER_LIGHT_PCT, "ความสว่าง Defect — วงนอก", False, 50),
    (_CH_INNER_LIGHT_PCT, "ความสว่าง Defect — วงใน",  False, 50),
]

# QSettings key — เก็บ "ค่าที่ส่งหลังบ้าน" (0–100) ตรงๆ ให้ pipeline อ่านส่ง Detection
_SETTINGS_KEY = "detection/{ch}/{size}"


def _channel_meta(ch: str) -> tuple[bool, int]:
    """คืน (flip, default_slider) ของ channel นี้"""
    for suffix, _label, flip, default in _THRESHOLD_CHANNELS:
        if suffix == ch:
            return flip, default
    return False, 0


def _load_channel(ch: str, size: str) -> int:
    """คืน 'ตำแหน่ง slider' (0–100) ของ channel/size นี้ (แปลงกลับจากค่าที่เก็บ)"""
    flip, default = _channel_meta(ch)
    s = QSettings()
    key = _SETTINGS_KEY.format(ch=ch, size=size)
    if s.contains(key):
        stored = int(float(s.value(key)))
        return (100 - stored) if flip else stored
    return default


def _save_channel(ch: str, size: str, slider_pos: int) -> None:
    """บันทึก channel ลง QSettings (เก็บ 'ค่าที่ส่งหลังบ้าน')"""
    flip, _default = _channel_meta(ch)
    sent = (100 - int(slider_pos)) if flip else int(slider_pos)
    s = QSettings()
    s.setValue(_SETTINGS_KEY.format(ch=ch, size=size), sent)
    s.sync()   # flush ให้ pipeline (QSettings instance แยก) อ่านเห็นทันที


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

        # ── Threshold sliders (MA Mode) — 4 หลอด: พื้นที่ นอก/ใน + แสง นอก/ใน ──
        self._threshold_frame = QFrame()
        self._threshold_frame.setObjectName("thresholdFrame")
        th_layout = QVBoxLayout(self._threshold_frame)
        th_layout.setContentsMargins(0, 6, 0, 0)
        th_layout.setSpacing(10)

        # หัวข้อ + ปุ่ม DEFAULT
        th_head = QHBoxLayout()
        th_title = QLabel("ความไวการตรวจจับ DEFECT  [MA MODE]")
        th_title.setObjectName("sectionLabel")
        th_head.addWidget(th_title)
        th_head.addStretch()
        self._default_btn = QPushButton("DEFAULT")
        self._default_btn.setObjectName("defaultBtn")
        self._default_btn.setFixedHeight(32)
        self._default_btn.setCursor(Qt.PointingHandCursor)
        self._default_btn.clicked.connect(self._on_reset_threshold)
        th_head.addWidget(self._default_btn)
        th_layout.addLayout(th_head)

        # สร้าง 4 หลอด
        self._sliders: dict[str, QSlider]  = {}
        self._value_labels: dict[str, QLabel] = {}
        for ch, label, _flip, default in _THRESHOLD_CHANNELS:
            th_layout.addLayout(self._make_threshold_channel(ch, label, default))

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
        self._target_spin.setReadOnly(True)   # กรอกผ่าน numpad เท่านั้น (จอ touchscreen ไม่มีคีย์บอร์ด)
        root.addWidget(self._target_spin)

        # Numpad — ป้อน Target บนจอ touchscreen (ไม่ต้องใช้คีย์บอร์ด)
        root.addWidget(NumPad(self._target_spin))

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

    # ── Threshold helpers ──────────────────────────────────────────────────

    def _make_threshold_channel(self, ch: str, label: str, default: int) -> QVBoxLayout:
        """สร้าง 1 หลอด: หัวข้อ (ซ้าย) + ค่า (ขวา) + slider ด้านล่าง"""
        box = QVBoxLayout()
        box.setSpacing(3)

        head = QHBoxLayout()
        name = QLabel(label)
        name.setObjectName("dimLabel")
        head.addWidget(name)
        head.addStretch()
        val = QLabel()
        val.setObjectName("channelValue")
        head.addWidget(val)
        box.addLayout(head)

        sld = QSlider(Qt.Horizontal)
        sld.setRange(_SLIDER_MIN, _SLIDER_MAX)
        sld.setSingleStep(_SLIDER_STEP)
        sld.setPageStep(_SLIDER_STEP * 2)
        sld.setValue(default)
        sld.setFixedHeight(28)
        sld.valueChanged.connect(lambda v, c=ch: self._on_slider_changed(c, v))
        box.addWidget(sld)

        self._sliders[ch] = sld
        self._value_labels[ch] = val
        self._update_value_label(ch, default)
        return box

    def _load_slider_for_size(self, size: str) -> None:
        """โหลดค่า QSettings ของ size นี้แล้วตั้ง slider ทุกหลอด"""
        for ch, sld in self._sliders.items():
            val = _load_channel(ch, size)
            sld.blockSignals(True)
            sld.setValue(val)
            sld.blockSignals(False)
            self._update_value_label(ch, val)

    def _update_value_label(self, ch: str, slider_pos: int) -> None:
        """อัปเดต label — แสง=ความสว่าง, พื้นที่=ความเข้มงวด (ส่ง % ตรงๆ ทั้งคู่)"""
        kind = "ความสว่าง" if "light" in ch else "ความเข้มงวด"
        self._value_labels[ch].setText(f"{kind} {slider_pos}%")

    # ── Slots ─────────────────────────────────────────────────────────────

    def _on_size_clicked(self, size: str) -> None:
        self._selected_size = size
        self._highlight_size(size)
        # อัปเดต slider เป็นค่าของ size ที่เลือก
        if self._threshold_mode == "on":
            self._load_slider_for_size(size)

    def _on_slider_changed(self, ch: str, value: int) -> None:
        # snap ให้เป็น step
        snapped = round(value / _SLIDER_STEP) * _SLIDER_STEP
        snapped = max(_SLIDER_MIN, min(_SLIDER_MAX, snapped))
        sld = self._sliders[ch]
        if snapped != value:
            sld.blockSignals(True)
            sld.setValue(snapped)
            sld.blockSignals(False)
        self._update_value_label(ch, snapped)

    def _on_reset_threshold(self) -> None:
        """รีเซ็ตทุกหลอดกลับ default — บันทึกตอน START"""
        for ch, _label, _flip, default in _THRESHOLD_CHANNELS:
            self._sliders[ch].setValue(default)
            self._update_value_label(ch, default)

    def _on_accept(self) -> None:
        """บันทึกทุกหลอดลง QSettings แล้ว accept"""
        if self._threshold_mode == "on":
            for ch, sld in self._sliders.items():
                _save_channel(ch, self._selected_size, sld.value())
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
            #channelValue {
                font-size: 13px;
                font-weight: bold;
                color: #1565c0;
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
