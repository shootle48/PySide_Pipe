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
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


# ── Logging setup (console + rotating file) ───────────────────────────────
def _setup_logging() -> None:
    """
    Configure root logger:
      - Console handler (เหมือนเดิม — สำหรับ dev)
      - Rotating file handler: logs/app.log, 5MB × 5 ไฟล์ (เก็บสูงสุด ~25MB)
    """
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — ใช้ format เดิม (เวลาสั้น) เพื่อให้อ่าน terminal ง่าย
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))

    # Rotating file handler — format เต็ม (มีวันที่) สำหรับ debug ย้อนหลัง
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=5,              # เก็บ app.log + app.log.1 ... app.log.5
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # ลบ handler เก่าที่อาจถูก basicConfig ใส่ไว้ (re-import safe)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(file_handler)


_setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Pipe Inspector")
    app.setOrganizationName("Research")

    window = MainWindow()
    window.show()

    logger.info("Pipe Inspector (PySide6) started.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
