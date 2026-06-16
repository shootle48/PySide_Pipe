"""paths.py — single source of truth for filesystem locations.

The legacy code resolved the project root with ``Path(__file__).parent.parent``
in three different places (database.py, main.py, maintenance_widget.py). That
breaks the moment a file moves to a different depth. This module resolves the
root *once*, robustly, so ``data/``, ``logs/``, ``exports/`` and the SQLite DB
always land in the same physical location regardless of import site.

Resolution order for the project root:
    1. ``PIPE_INSPECTOR_ROOT`` env var (explicit override — handy in Docker)
    2. walk upward from this file until a marker (pyproject.toml / .git) is found
    3. fall back to this file's grandparent

Individual directories can also be overridden via ``PIPE_INSPECTOR_*`` env vars,
which keeps the SQLite DB path identical to the legacy layout by default:
``<root>/data/pipe_inspector.db``.
"""

from __future__ import annotations

import os
from pathlib import Path

_MARKERS = ("pyproject.toml", ".git")


def _find_root(start: Path) -> Path:
    """Walk up from *start* returning the first dir containing a project marker."""
    for parent in (start, *start.parents):
        if any((parent / marker).exists() for marker in _MARKERS):
            return parent
    return start


def _resolve_root() -> Path:
    env = os.environ.get("PIPE_INSPECTOR_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # this file lives at <root>/src/pipe_inspector/paths.py
    return _find_root(Path(__file__).resolve().parent)


def _dir(env_key: str, default: Path) -> Path:
    env = os.environ.get(env_key)
    return Path(env).expanduser().resolve() if env else default


PROJECT_ROOT: Path = _resolve_root()
DATA_DIR: Path = _dir("PIPE_INSPECTOR_DATA_DIR", PROJECT_ROOT / "data")
LOGS_DIR: Path = _dir("PIPE_INSPECTOR_LOGS_DIR", PROJECT_ROOT / "logs")
EXPORTS_DIR: Path = _dir("PIPE_INSPECTOR_EXPORTS_DIR", PROJECT_ROOT / "exports")
DB_PATH: Path = _dir("PIPE_INSPECTOR_DB_PATH", DATA_DIR / "pipe_inspector.db")


def ensure_runtime_dirs() -> None:
    """Create data/logs/exports dirs if missing (DB parent included)."""
    for directory in (DATA_DIR, LOGS_DIR, EXPORTS_DIR, DB_PATH.parent):
        directory.mkdir(parents=True, exist_ok=True)
