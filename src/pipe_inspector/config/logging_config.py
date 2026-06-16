"""logging_config.py — root logging setup (console + two rotating files).

Extracted verbatim from the old ``main._setup_logging`` so behaviour is
identical; only the log directory now comes from :mod:`pipe_inspector.paths`
instead of ``Path(__file__).parent / "logs"``.

    logs/app.log         — everything (both sides)
    logs/smartsense.log  — only loggers named "smartsense.*" (hardware / library)
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from pipe_inspector import paths


class _SmartSenseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("smartsense")


def setup_logging() -> None:
    log_dir = paths.LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    full_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    short_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(short_fmt)

    app_file = RotatingFileHandler(
        log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    app_file.setFormatter(full_fmt)

    smartsense_file = RotatingFileHandler(
        log_dir / "smartsense.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    smartsense_file.setFormatter(full_fmt)
    smartsense_file.addFilter(_SmartSenseFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(console)
    root.addHandler(app_file)
    root.addHandler(smartsense_file)
