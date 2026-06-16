"""
utils.py
────────
Pure utility functions shared across core/ and ui/ modules.

No Qt dependency — safe to import in unit tests without a QApplication.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Replaces the repeated ``datetime.now(timezone.utc).isoformat()`` pattern.
    """
    return datetime.now(timezone.utc).isoformat()


def iso_to_local_str(iso: str, fmt: str = "%H:%M:%S") -> str:
    """Parse an ISO-8601 UTC timestamp and format it as local time.

    Args:
        iso: ISO-8601 string, e.g. ``"2024-01-15T08:30:00+00:00"``.
        fmt: strftime format for the output.  Default ``"%H:%M:%S"``.

    Returns:
        Formatted local-time string, or the original ``iso`` string on parse error.
    """
    try:
        return datetime.fromisoformat(iso).astimezone().strftime(fmt)
    except (ValueError, OSError):
        return iso


def clamp(value: float, lo: float, hi: float) -> float:
    """Return ``value`` clamped to ``[lo, hi]``."""
    return max(lo, min(hi, value))
