"""app.py — application bootstrap (QApplication + MainWindow).

Moved from the old root main.py during the standard-layout refactor. Entry
points that reach here:
    python main.py            (thin root shim, kept for autostart/systemd)
    python -m pipe_inspector  (via __main__.py)
    pipe-inspector            (console script from pyproject [project.scripts])
"""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtWidgets import QApplication, QMessageBox

from pipe_inspector.config.logging_config import setup_logging
from pipe_inspector.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    t_launch = time.perf_counter()

    app = QApplication(sys.argv)
    # ⚠️ ต้องตั้ง org + app name คู่กัน — ไม่งั้น QSettings() ไม่มี scope ที่ persist
    #    (ทำให้ค่า threshold/camera/calibration save ไม่ติด = "ใช้ default อย่างเดียว")
    app.setOrganizationName("SmartSense")
    app.setApplicationName("Pipe Inspector")

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

    window.showFullScreen()
    logger.info("Startup: app ready in %.0f ms (total)", (time.perf_counter() - t_launch) * 1000)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
