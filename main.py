"""main.py — thin entry-point shim.

The real bootstrap lives in ``pipe_inspector.app.main``. This file is kept at the
repo root so the familiar ``python main.py`` (and any existing autostart / systemd
units that reference it) keep working unchanged after the standard-layout refactor.

Equivalent entry points:
    python main.py
    python -m pipe_inspector
    pipe-inspector            # console script (see pyproject [project.scripts])
"""

import sys
from pathlib import Path

# Make the src/ layout importable so `python main.py` works even without
# `pip install -e .` — e.g. a fresh clone, a Jetson, or a venv that doesn't
# have the package installed yet.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pipe_inspector.app import main  # noqa: E402  (import after sys.path bootstrap)

if __name__ == "__main__":
    main()
