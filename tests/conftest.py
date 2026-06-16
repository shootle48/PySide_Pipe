"""Shared pytest fixtures.

`pythonpath = ["src"]` in pyproject.toml makes `import pipe_inspector` work
without an editable install, so these unit tests run headless (no QApplication,
no camera, no hardware) — suitable for CI / Docker.
"""

from __future__ import annotations
