"""Tests for pipe_inspector.config.settings.

Guards that the centralized defaults still match the legacy hardcoded values
from ui/main_window.py (CAMERA_INDEX=2, TRIGGER_MODE=manual, …) and that the
YAML + env override precedence works.
"""

from __future__ import annotations

from pathlib import Path

from pipe_inspector.config.settings import Settings, load_settings


def test_defaults_match_legacy_constants():
    s = Settings()
    assert s.camera.index == 2
    assert s.trigger.mode == "manual"
    assert s.trigger.timer_interval == 6.0
    assert s.ui.result_view_secs == 4
    assert s.rs485.mode == "off"
    assert s.rs485.watch_bits == [0]
    assert s.rs485.ng_output_bit == 1
    assert s.detection.threshold_mode == "on"
    assert s.detection.defaults.outer_pct == 50.0
    assert s.detection.defaults.outer_light_pct == 40.0


def test_yaml_overlay_partial(tmp_path: Path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("camera:\n  index: 5\ntrigger:\n  mode: timer\n", encoding="utf-8")
    s = load_settings(cfg)
    assert s.camera.index == 5
    assert s.trigger.mode == "timer"
    # keys not present in the file keep their defaults
    assert s.ui.result_view_secs == 4
    assert s.rs485.mode == "off"


def test_unknown_keys_ignored(tmp_path: Path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("camera:\n  index: 3\n  bogus: 99\nfuture_section: {}\n", encoding="utf-8")
    s = load_settings(cfg)  # must not raise
    assert s.camera.index == 3


def test_env_override_takes_precedence(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("camera:\n  index: 5\n", encoding="utf-8")
    monkeypatch.setenv("PIPE_INSPECTOR_CAMERA_INDEX", "7")
    s = load_settings(cfg)
    assert s.camera.index == 7


def test_shipped_settings_yaml_loads_and_matches_defaults():
    # config/settings.yaml must parse and agree with the legacy defaults.
    s = load_settings()
    assert s.camera.index == 2
    assert s.trigger.timer_interval == 6.0
    assert s.rs485.ng_output_bit == 1
