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

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

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
