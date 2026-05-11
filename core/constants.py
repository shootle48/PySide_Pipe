"""
constants.py
────────────
Single source of truth for all project-wide enumerations and colour mappings.

Import pattern:
    from core.constants import Verdict, TriggerMode, WorkerStatus
    from core.constants import VERDICT_COLORS, STATUS_COLORS, MONO_FONT
"""

from __future__ import annotations

from enum import Enum


# ── Domain enumerations ────────────────────────────────────────────────────

class Verdict(str, Enum):
    """Inspection verdict.  str mixin → passes directly into Qt labels / DB strings."""
    OK = "OK"
    NG = "NG"


class TriggerMode(str, Enum):
    """Source that fires the inspection cycle."""
    MANUAL = "manual"   # UI button or RS-485 pulse
    TIMER  = "timer"    # automatic, every TIMER_INTERVAL seconds
    GPIO   = "gpio"     # Jetson GPIO rising-edge (production Jetson only)


class WorkerStatus(str, Enum):
    """CameraWorker lifecycle states emitted via status_changed signal."""
    IDLE       = "idle"
    SCANNING   = "scanning"     # capture delay — pipe settling
    PROCESSING = "processing"   # CV inference running
    ERROR      = "error"        # non-fatal error; worker returns to IDLE after


# ── Colour palettes ────────────────────────────────────────────────────────

#: Verdict → background hex colour (used in badges and history rows)
VERDICT_COLORS: dict[str, str] = {
    Verdict.OK: "#2e7d32",   # dark green (WCAG AA on white)
    Verdict.NG: "#c62828",   # dark red
}

#: WorkerStatus → HMI status-dot colour
STATUS_COLORS: dict[str, str] = {
    WorkerStatus.IDLE:       "#52606d",   # medium gray
    WorkerStatus.SCANNING:   "#0288d1",   # deep cyan/blue
    WorkerStatus.PROCESSING: "#ef6c00",   # dark orange
    WorkerStatus.ERROR:      "#c62828",   # dark red
    "connected":             "#2e7d32",   # dark green (camera online)
    "offline":               "#c62828",
}


# ── Typography ─────────────────────────────────────────────────────────────

MONO_FONT = "Consolas"   # monospace font for piece IDs, timestamps, confidence values
