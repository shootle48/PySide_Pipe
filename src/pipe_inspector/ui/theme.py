"""theme.py — HMI colour palettes and fonts.

Moved from core/constants.py during the standard-layout refactor (these are a
UI concern, not domain). The verdict/status keys reference the domain enums so
existing lookups like ``VERDICT_COLORS[Verdict.OK]`` keep working unchanged.
"""

from __future__ import annotations

from pipe_inspector.domain.enums import Verdict, WorkerStatus

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

#: monospace font for piece IDs, timestamps, confidence values
MONO_FONT = "Consolas"
