"""main.py — thin entry-point shim.

The real bootstrap lives in ``pipe_inspector.app.main``. This file is kept at the
repo root so the familiar ``python main.py`` (and any existing autostart / systemd
units that reference it) keep working unchanged after the standard-layout refactor.

Equivalent entry points:
    python main.py
    python -m pipe_inspector
    pipe-inspector            # console script (see pyproject [project.scripts])
"""

from pipe_inspector.app import main

if __name__ == "__main__":
    main()
