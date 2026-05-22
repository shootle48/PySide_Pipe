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
import sys
# import PySide6
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
    app = QApplication(sys.argv)
    app.setApplicationName("Pipe Inspector")

    try:
        window = MainWindow()
    except (RuntimeError, ImportError, OSError, ValueError) as exc:
        logger.critical("Startup failed: %s", exc, exc_info=True)
        QMessageBox.critical(
            None,
            "Startup Error",
            f"ไม่สามารถเริ่มต้นโปรแกรมได้:\n\n{exc}\n\nดู logs/app.log สำหรับรายละเอียด",
        )
        sys.exit(1)

    window.show()
    logger.info("Pipe Inspector (PySide6) started.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
