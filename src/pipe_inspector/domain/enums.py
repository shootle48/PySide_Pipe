"""enums.py — project-wide domain enumerations.

Moved from core/constants.py during the standard-layout refactor. Colour/font
palettes that used to live alongside these enums now live in
``pipe_inspector.ui.theme`` (UI concern).

Import pattern:
    from pipe_inspector.domain.enums import Verdict, TriggerMode, WorkerStatus
"""

from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """Inspection verdict.  str mixin → passes directly into Qt labels / DB strings."""
    OK = "OK"
    NG = "NG"


class TriggerMode(str, Enum):
    """Source that fires the inspection cycle."""
    MANUAL = "manual"   # UI button or RS-485 pulse
    TIMER  = "timer"    # automatic, every timer_interval seconds
    GPIO   = "gpio"     # Jetson GPIO rising-edge (production Jetson only)


class WorkerStatus(str, Enum):
    """CameraWorker lifecycle states emitted via status_changed signal."""
    IDLE       = "idle"
    SCANNING   = "scanning"     # capture delay — pipe settling
    PROCESSING = "processing"   # CV inference running
    ERROR      = "error"        # non-fatal error; worker returns to IDLE after
