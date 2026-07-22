"""
main.py  (PySide6 Pipe Inspector)
─────────────────────────────────
Entry point. Creates the QApplication and launches MainWindow.

Run:
    cd pipe-inspector-pyside
    python main.py

Config (edit ui/main_window.py top section):
    CAMERA_INDEX   = 1        # USB camera index (run camera_check.py if unsure)
    TRIGGER_MODE   = "manual" # "manual" | "timer" | "gpio"
    TIMER_INTERVAL = 6.0      # seconds (timer mode only)
"""

from __future__ import annotations

import logging
import signal
import sys
import tempfile
# import PySide6
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore    import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.main_window import MainWindow


# ── Logging setup (console + rotating file × 2) ───────────────────────────
def _setup_logging() -> None:
    """
    Configure root logger + แยก log ออกเป็น 2 ไฟล์ตามฝั่ง:

      logs/app.log         — ทุกอย่าง (รวมทั้งสองฝั่ง)
      logs/smartsense.log  — เฉพาะฝั่ง Smart-Sense library / hardware
                            (logger name ขึ้นต้นด้วย "smartsense.")

    เวลามีปัญหาที่หน้างาน:
      - ปัญหา detection / UI / state         → ดู app.log
      - ปัญหา trigger ไม่เข้า / sorter ไม่ทำงาน → ดู smartsense.log
        (ถ้า smartsense.log มี ERROR → ปัญหาอยู่ฝั่ง hardware/library Smart-Sense)
        (ถ้า smartsense.log clean แต่ app.log ERROR → ปัญหาฝั่งโค้ดผม)
    """
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    full_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    short_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Console handler (เหมือนเดิม — รับทุก log) ────────────────────────
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(short_fmt)

    # ── app.log — รวมทุก log จากทั้งสองฝั่ง ──────────────────────────────
    app_file = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    app_file.setFormatter(full_fmt)

    # ── smartsense.log — เฉพาะฝั่ง Smart-Sense ───────────────────────────
    # Filter: รับเฉพาะ logger ที่ขึ้นต้นด้วย "smartsense."
    class SmartSenseFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.name.startswith("smartsense")

    smartsense_file = RotatingFileHandler(
        log_dir / "smartsense.log",
        maxBytes=2 * 1024 * 1024,   # 2 MB (แยกไฟล์เล็กกว่า)
        backupCount=3,
        encoding="utf-8",
    )
    smartsense_file.setFormatter(full_fmt)
    smartsense_file.addFilter(SmartSenseFilter())

    # ── Mount handlers บน root logger ────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(app_file)
    root.addHandler(smartsense_file)


_setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    import time
    t_launch = time.perf_counter()

    app = QApplication(sys.argv)
    # ⚠️ ต้องตั้ง org + app name คู่กัน — ไม่งั้น QSettings() ไม่มี scope ที่ persist
    #    (ทำให้ค่า threshold/camera/calibration save ไม่ติด = "ใช้ default อย่างเดียว")
    app.setOrganizationName("SmartSense")
    app.setApplicationName("Pipe Inspector")

    # ── Single-instance lock ────────────────────────────────────────────────
    # กันเปิดซ้อน 2 instance (operator ดับเบิลคลิกไอคอน / autostart เปิดแล้วกดไอคอนซ้ำ)
    # → กันแย่งกล้อง + เขียน DB/counter ชนกัน (piece_id = COUNT+1 จาก 2 proc จะชน)
    # QLockFile เช็ค PID ให้เอง: ถ้า instance เก่า crash ค้าง lock → tryLock รู้ว่า
    # process ตายแล้วและยึด lock ต่อได้ → operator restart ผ่านไอคอนได้เสมอ ไม่ค้างถาวร
    instance_lock = QLockFile(str(Path(tempfile.gettempdir()) / "pipe-inspector.lock"))
    if not instance_lock.tryLock(100):
        logger.warning("Another instance is already running — exiting.")
        QMessageBox.warning(
            None, "Pipe Inspector",
            "โปรแกรมกำลังเปิดอยู่แล้ว — ไม่เปิดซ้ำ\n(กันกล้อง / ฐานข้อมูลชนกัน)",
        )
        sys.exit(0)

    try:
        t0 = time.perf_counter()
        window = MainWindow()
        logger.info("Startup: MainWindow init %.0f ms", (time.perf_counter() - t0) * 1000)
    except (RuntimeError, ImportError, OSError, ValueError) as exc:
        logger.critical("Startup failed: %s", exc, exc_info=True)
        QMessageBox.critical(
            None,
            "Startup Error",
            f"ไม่สามารถเริ่มต้นโปรแกรมได้:\n\n{exc}\n\nดู logs/app.log สำหรับรายละเอียด",
        )
        sys.exit(1)

    # ── ปิดสะอาดเมื่อระบบสั่ง (reboot / shutdown / pkill) ───────────────────
    # kiosk mode บล็อก closeEvent ไว้ → ถ้าไม่ดัก SIGTERM เครื่องจะ reboot ค้างรอแอป
    # (Qt ไม่ประมวลผล signal ระหว่าง event loop → ใช้ QTimer เตะกลับเข้า loop ก่อนสั่งปิด)
    def _on_term(signum, _frame) -> None:
        logger.info("ได้รับ signal %s — ปิดโปรแกรม", signum)
        window.authorize_exit()
        QTimer.singleShot(0, window.close)

    for _sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(_sig, _on_term)
    # timer เปล่าทุก 500ms — ปลุก Python ให้ได้รัน signal handler (ไม่งั้นค้างใน C++ event loop)
    _signal_wakeup = QTimer()
    _signal_wakeup.start(500)
    _signal_wakeup.timeout.connect(lambda: None)

    window.showFullScreen()
    logger.info("Startup: app ready in %.0f ms (total)", (time.perf_counter() - t_launch) * 1000)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
