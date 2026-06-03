"""
test_rs485_roundtrip.py
───────────────────────
ทดสอบ RS485 round-trip บน Windows (ไม่ต้องมี hardware)

จำลองสถานการณ์จริง:
  1. กดปุ่ม "Send Trigger"   → MockRS485DIO ส่ง pulse ที่ input bit 0
                                (= Smart-Sense PLC ส่ง trigger เข้ามา)
  2. RS485InputWorker จับ rising edge → emit pulse_detected
  3. โค้ดสุ่มผล verdict (OK/NG) → จำลองผลจาก PipeInspector
  4. RS485OutputWriter เรียก write_pulse กลับ
       - bit 0 = OK
       - bit 1 = NG
  5. MockRS485DIO emit output_written → test panel แสดงว่า "PLC ได้รับผล"

รัน:
    python scripts/test_rs485_roundtrip.py

ดูผลที่:
  - หน้าต่าง test panel — เห็น input/output แบบ real-time
  - logs/app.log         — log ฝั่งโค้ดผม (app.*)
  - logs/smartsense.log  — log ฝั่ง Smart-Sense library (smartsense.*)
"""

from __future__ import annotations

import logging
import random
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Allow import จาก project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force UTF-8 console บน Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from PySide6.QtCore    import Qt, QTimer
from PySide6.QtGui     import QFont
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QVBoxLayout, QWidget,
)

from core.rs485_worker import (
    MockRS485DIO, RS485InputWorker, RS485OutputWriter,
)


# ══════════════════════════════════════════════════════════════════════════
# Logging — แยก app.log กับ smartsense.log
# ══════════════════════════════════════════════════════════════════════════

def _setup_logging() -> None:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)

    app_file = RotatingFileHandler(
        log_dir / "app.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    app_file.setFormatter(fmt)

    class SmartSenseFilter(logging.Filter):
        def filter(self, record): return record.name.startswith("smartsense")

    ss_file = RotatingFileHandler(
        log_dir / "smartsense.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    ss_file.setFormatter(fmt)
    ss_file.addFilter(SmartSenseFilter())

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(app_file)
    root.addHandler(ss_file)


_setup_logging()
logger = logging.getLogger("app.test_panel")


# ══════════════════════════════════════════════════════════════════════════
# Test Panel — UI สำหรับทดสอบ
# ══════════════════════════════════════════════════════════════════════════

class RS485TestPanel(QWidget):
    """
    หน้าต่างทดสอบ RS485 round-trip
    ┌─────────────────────────────────────────────────┐
    │  RS485 ROUND-TRIP TEST                          │
    │                                                 │
    │  [📤 Send Trigger]   จำลอง PLC ยิง pulse        │
    │                                                 │
    │  Status: ● IDLE                                 │
    │                                                 │
    │  ── Event log ──                                │
    │  14:23:01  TRIGGER received (bit 0)             │
    │  14:23:01  → simulating inspection...           │
    │  14:23:02  ← verdict = NG                       │
    │  14:23:02  PLC received: NG (bit 1 pulse)       │
    └─────────────────────────────────────────────────┘
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RS485 Round-trip Test  —  Pipe Inspector")
        self.resize(640, 520)

        # ── Smart-Sense convention (ตามที่บิ๊กจะตกลงกับทีม) ─────────────
        # Input bit 0 = trigger pulse จาก PLC
        # Output bit 0 = OK,  Output bit 1 = NG
        self.TRIGGER_BIT = 0
        self.OK_BIT      = 0
        self.NG_BIT      = 1

        self._build_ui()
        self._setup_rs485()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        # Title
        title = QLabel("RS485 ROUND-TRIP TEST")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        root.addWidget(title)

        subtitle = QLabel(
            "จำลอง Smart-Sense PLC ยิง trigger → ระบบตรวจจับ → ส่ง verdict กลับ"
        )
        subtitle.setStyleSheet("color: #52606d;")
        root.addWidget(subtitle)

        # Trigger button (big, factory-style)
        self.trigger_btn = QPushButton("📤  SEND TRIGGER  (จำลอง PLC ยิง pulse)")
        self.trigger_btn.setFixedHeight(56)
        self.trigger_btn.setStyleSheet("""
            QPushButton {
                background: #1565c0; color: white; font-size: 16px;
                font-weight: bold; border-radius: 8px; padding: 8px;
            }
            QPushButton:hover { background: #1976d2; }
            QPushButton:pressed { background: #0d47a1; }
        """)
        self.trigger_btn.clicked.connect(self._send_trigger)
        root.addWidget(self.trigger_btn)

        # Status row
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_label = QLabel("● IDLE")
        self.status_label.setStyleSheet("color: #52606d; font-weight: bold;")
        status_row.addWidget(self.status_label)
        status_row.addStretch()

        # Counters
        self.ok_count_label = QLabel("OK: 0")
        self.ng_count_label = QLabel("NG: 0")
        self.ok_count_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        self.ng_count_label.setStyleSheet("color: #c62828; font-weight: bold;")
        status_row.addWidget(self.ok_count_label)
        status_row.addWidget(QLabel("  "))
        status_row.addWidget(self.ng_count_label)
        root.addLayout(status_row)

        # Divider
        line = QFrame(); line.setFrameShape(QFrame.HLine)
        root.addWidget(line)

        root.addWidget(QLabel("Event log (real-time):"))
        self.log_list = QListWidget()
        self.log_list.setFont(QFont("Consolas", 10))
        root.addWidget(self.log_list, stretch=1)

        # Footer hint
        footer = QLabel(
            "💡 ดู log file ด้วย:  logs/app.log  +  logs/smartsense.log"
        )
        footer.setStyleSheet("color: #52606d; font-size: 10px;")
        root.addWidget(footer)

        # Counters state
        self._ok_count = 0
        self._ng_count = 0

    # ── RS485 wiring ──────────────────────────────────────────────────────

    def _setup_rs485(self) -> None:
        # 1. สร้าง mock DIO (Windows test — ไม่ต้องมี hardware)
        self.dio = MockRS485DIO(mode="manual")

        # 2. Input worker — จับ trigger จาก bit 0
        self.input_worker = RS485InputWorker(
            io=self.dio,
            watch_bits=[self.TRIGGER_BIT],
            poll_interval_s=0.02,
            debounce_ms=30,
        )
        self.input_worker.pulse_detected.connect(self._on_trigger_received)
        self.input_worker.io_health_changed.connect(self._on_health_changed)
        self.input_worker.start()

        # 3. Output writer — ส่ง verdict กลับ
        self.output_writer = RS485OutputWriter(
            io=self.dio,
            ok_bit=self.OK_BIT,
            ng_bit=self.NG_BIT,
            pulse_ms=200,
        )

        # 4. Subscribe MockRS485DIO output_written → แสดงใน log
        self.dio.output_written.connect(self._on_output_observed)

        self._append_log("System ready  —  รอ trigger จาก PLC", "#1565c0")

    # ── Slots ─────────────────────────────────────────────────────────────

    def _send_trigger(self) -> None:
        """User กดปุ่ม → จำลอง Smart-Sense ส่ง pulse เข้ามา"""
        self._append_log(
            f"►►► PLC ยิง trigger pulse ที่ bit {self.TRIGGER_BIT}", "#1565c0"
        )
        self.dio.pulse_input(bit=self.TRIGGER_BIT, pulse_width_s=0.1)

    def _on_trigger_received(self, bit: int) -> None:
        """RS485InputWorker จับ rising edge ได้ → เริ่ม inspection"""
        self.status_label.setText("● INSPECTING...")
        self.status_label.setStyleSheet("color: #1565c0; font-weight: bold;")
        self._append_log(f"   ✓ TRIGGER detected (bit {bit})", "#2e7d32")
        self._append_log("   ⏱  simulating inspection (100ms)...", "#52606d")

        # จำลอง inspection delay (ของจริงคือ PipeInspector.inspect)
        QTimer.singleShot(100, self._simulate_inspection)

    def _simulate_inspection(self) -> None:
        """แทน PipeInspector — สุ่ม OK/NG (60% OK, 40% NG)"""
        verdict = "OK" if random.random() < 0.6 else "NG"
        color   = "#2e7d32" if verdict == "OK" else "#c62828"
        self._append_log(f"   ◀ verdict = {verdict}", color)

        # ส่งกลับ PLC
        self.output_writer.send_verdict(verdict)

        # Reset status
        QTimer.singleShot(300, self._reset_status)

    def _on_output_observed(self, bit: int, value: int) -> None:
        """MockRS485DIO emit เมื่อมีการ write_output → ฝั่ง 'PLC' ได้รับ"""
        if value == 0:
            return   # ignore LOW edge ของ pulse (เหลือแค่ HIGH event)

        if bit == self.OK_BIT:
            self._ok_count += 1
            self.ok_count_label.setText(f"OK: {self._ok_count}")
            self._append_log(
                f"      📥 PLC received: OK pulse (bit {bit})", "#2e7d32"
            )
        elif bit == self.NG_BIT:
            self._ng_count += 1
            self.ng_count_label.setText(f"NG: {self._ng_count}")
            self._append_log(
                f"      📥 PLC received: NG pulse (bit {bit})  → reject sorter ทำงาน",
                "#c62828",
            )

    def _reset_status(self) -> None:
        self.status_label.setText("● IDLE")
        self.status_label.setStyleSheet("color: #52606d; font-weight: bold;")

    def _on_health_changed(self, online: bool) -> None:
        msg = "I/O ONLINE" if online else "I/O OFFLINE"
        self._append_log(f"!! {msg} !!", "#c62828" if not online else "#2e7d32")

    # ── Helpers ───────────────────────────────────────────────────────────

    def _append_log(self, msg: str, color: str = "#1a1d23") -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        item_text = f"{ts}  {msg}"
        self.log_list.addItem(item_text)
        # set color ของ item ล่าสุด
        from PySide6.QtGui import QColor
        last = self.log_list.item(self.log_list.count() - 1)
        last.setForeground(QColor(color))
        self.log_list.scrollToBottom()

    # ── Cleanup ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        logger.info("Test panel closing — stopping worker")
        self.input_worker.stop()
        self.input_worker.wait(2000)
        self.dio.stop()
        super().closeEvent(event)


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    panel = RS485TestPanel()
    panel.show()
    sys.exit(app.exec())
