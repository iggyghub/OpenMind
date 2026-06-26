"""
Settings store — owns cerebral/data/felix-settings.json.

The Main window (ADR-0007) cannot call Node fs APIs directly, so all
settings must be read and written via WebSocket to Cerebral.

Keys: notifications_enabled, reminder_interval_minutes, camera_enabled,
      visualiser_visible, mic_mode, tts_muted, tts_volume,
      mic_input_device, browser_pause_on_verification
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "notifications_enabled":     False,
    "reminder_interval_minutes": 120,
    "camera_enabled":            False,
    "visualiser_visible":        False,
    "mic_mode":                  "passive",
    "tts_muted":                 False,
    "tts_volume":                100,
    "mic_input_device":          "",
    # When a browser-automation tool hits a human-verification wall (e.g.
    # Google's "verify it's you" step-up), pause and notify the user to clear
    # it in a visible window rather than silently failing. Default ON — failing
    # loud beats stalling.
    "browser_pause_on_verification": True,
}

_VALID_KEYS: frozenset[str] = frozenset(_DEFAULTS)

# Expected Python type for each setting key.
_TYPES: dict[str, type] = {
    "notifications_enabled":     bool,
    "reminder_interval_minutes": int,
    "camera_enabled":            bool,
    "visualiser_visible":        bool,
    "mic_mode":                  str,
    "tts_muted":                 bool,
    "tts_volume":                int,
    "mic_input_device":          str,
    "browser_pause_on_verification": bool,
}

_MIC_MODE_VALUES: frozenset[str] = frozenset({"passive", "ptt", "disabled"})

_SETTINGS_PATH = Path(__file__).parent / "data" / "felix-settings.json"


class SettingsStore:
    """Thin JSON-backed store for system settings.

    Designed as a process-wide singleton in main.py.  Thread-safe enough
    for the single asyncio event loop — all reads/writes happen there.
    """

    def __init__(self, path: Path = _SETTINGS_PATH) -> None:
        self._path = path
        self._data: dict[str, Any] = self._load()

    # ── public ─────────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        """Validate and persist a setting.  Raises ``ValueError`` on bad
        key or incompatible type."""
        if key not in _VALID_KEYS:
            raise ValueError(f"Unknown setting key: {key!r}")
        expected = _TYPES[key]
        # JSON numbers deserialise as int; coerce to bool for boolean keys.
        if expected is bool and isinstance(value, int) and not isinstance(value, bool):
            value = bool(value)
        if not isinstance(value, expected):
            raise ValueError(
                f"Setting {key!r} expects {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        if key == "reminder_interval_minutes":
            value = max(0, value)
        if key == "tts_volume":
            value = max(0, min(100, value))
        if key == "mic_mode" and value not in _MIC_MODE_VALUES:
            raise ValueError(
                f"mic_mode must be one of {sorted(_MIC_MODE_VALUES)}, got {value!r}"
            )
        self._data[key] = value
        self._save()

    def all(self) -> dict[str, Any]:
        """Return a snapshot of all settings."""
        return {k: self._data.get(k, v) for k, v in _DEFAULTS.items()}

    # ── private ────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {
                **_DEFAULTS,
                **{k: v for k, v in raw.items() if k in _VALID_KEYS},
            }
        except FileNotFoundError:
            return dict(_DEFAULTS)
        except Exception:
            logger.warning(
                "[settings] Could not read %s; using defaults", self._path
            )
            return dict(_DEFAULTS)

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.warning("[settings] Could not write %s", self._path)
