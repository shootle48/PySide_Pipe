"""settings.py — typed application settings.

Replaces the hardcoded config block that used to live at the top of
``ui/main_window.py`` (CAMERA_INDEX, TRIGGER_MODE, RS485_MODE, …) and the
duplicate ``TIMER_INTERVAL`` in ``core/pipeline.py``.

Precedence (low → high):
    built-in dataclass defaults  <  config/settings.yaml  <  PIPE_INSPECTOR_* env

QSettings (Qt) is unchanged and still holds *user* runtime overrides (detection
thresholds per size, the camera index picked in the UI). This module only owns
the static startup defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from pipe_inspector import paths


@dataclass
class CameraConfig:
    index: int = 2                       # was CAMERA_INDEX


@dataclass
class TriggerConfig:
    mode: str = "manual"                 # manual | timer | gpio  (was TRIGGER_MODE)
    timer_interval: float = 6.0          # was TIMER_INTERVAL


@dataclass
class UIConfig:
    result_view_secs: int = 4            # was RESULT_VIEW_SECS


@dataclass
class RS485Config:
    mode: str = "off"                    # off | mock | real  (was RS485_MODE)
    watch_bits: list[int] = field(default_factory=lambda: [0])  # was RS485_WATCH_BITS
    ng_output_bit: int = 1               # was RS485_NG_OUTPUT_BIT


@dataclass
class DetectionDefaults:
    outer_pct: float = 50.0
    inner_pct: float = 50.0
    outer_light_pct: float = 40.0
    inner_light_pct: float = 40.0


@dataclass
class DetectionConfig:
    threshold_mode: str = "on"           # on | off  (was DETECTION_THRESHOLD_MODE)
    defaults: DetectionDefaults = field(default_factory=DetectionDefaults)


@dataclass
class Settings:
    camera: CameraConfig = field(default_factory=CameraConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    rs485: RS485Config = field(default_factory=RS485Config)
    detection: DetectionConfig = field(default_factory=DetectionConfig)


# ── Loading ────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS_PATH: Path = paths.PROJECT_ROOT / "config" / "settings.yaml"

#: env key → (section attr, field attr, cast)
_ENV_OVERRIDES: dict[str, tuple[str, str, Any]] = {
    "PIPE_INSPECTOR_CAMERA_INDEX": ("camera", "index", int),
    "PIPE_INSPECTOR_TRIGGER_MODE": ("trigger", "mode", str),
    "PIPE_INSPECTOR_RS485_MODE": ("rs485", "mode", str),
}


def _overlay(obj: Any, data: Any) -> None:
    """Recursively overlay a plain dict onto a dataclass instance (in place).

    Unknown keys are ignored so adding fields to settings.yaml never crashes an
    older build (forward-compatible).
    """
    if not is_dataclass(obj) or not isinstance(data, dict):
        return
    valid = {f.name for f in fields(obj)}
    for key, value in data.items():
        if key not in valid:
            continue
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _overlay(current, value)
        else:
            setattr(obj, key, value)


def _apply_env(settings: Settings) -> None:
    for env_key, (section, attr, cast) in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is not None and raw != "":
            setattr(getattr(settings, section), attr, cast(raw))


def load_settings(path: str | Path | None = None) -> Settings:
    """Build a :class:`Settings` from defaults + YAML file + env overrides."""
    settings = Settings()
    cfg_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    if cfg_path.is_file():
        with cfg_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _overlay(settings, data)
    _apply_env(settings)
    return settings
