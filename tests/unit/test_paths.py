"""Tests for pipe_inspector.paths — project-root + DB-location resolution.

The critical invariant of the whole re-layout: the SQLite DB must keep
resolving to <project-root>/data/pipe_inspector.db so existing history is not
"lost" when files move into src/.
"""

from __future__ import annotations

from pathlib import Path

from pipe_inspector import paths


def test_project_root_has_marker():
    # PROJECT_ROOT must be the real repo root (contains pyproject.toml).
    assert (paths.PROJECT_ROOT / "pyproject.toml").is_file()


def test_db_path_under_data_dir_and_named_correctly():
    assert paths.DB_PATH == paths.DATA_DIR / "pipe_inspector.db"
    assert paths.DB_PATH.name == "pipe_inspector.db"


def test_data_dir_defaults_under_root():
    assert paths.DATA_DIR == paths.PROJECT_ROOT / "data"


def test_find_root_walks_up_to_marker(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    nested = tmp_path / "src" / "pipe_inspector"
    nested.mkdir(parents=True)
    assert paths._find_root(nested) == tmp_path


def test_find_root_falls_back_when_no_marker(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    # no marker anywhere under tmp_path → returns the start dir unchanged
    assert paths._find_root(nested) == nested
