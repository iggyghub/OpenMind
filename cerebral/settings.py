"""
Settings store — owns cerebral/data/felix-settings.json.

The Main window (ADR-0007) cannot call Node fs APIs directly, so all
settings must be read and written via WebSocket to Cerebral.

Keys: notifications_enabled, reminder_interval_minutes, camera_enabled,
      visualiser_visible, mic_mode, tts_muted, tts_volume,
      mic_input_device, browser_pause_on_verification,
      disabled_plugins, enabled_skills, background_actuation,
      setvalue_roles, user_idle_ms, trading_live_arm
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
    # Push-to-talk global hotkey (Electron accelerator string). Used by the
    # tray to register a globalShortcut when mic_mode == "ptt"; pressing it
    # triggers a capture with no wake word. User-rebindable from the UI.
    "ptt_key":                   "Control+Shift+Space",
    "tts_muted":                 False,
    "tts_volume":                100,
    "mic_input_device":          "",
    # When a browser-automation tool hits a human-verification wall (e.g.
    # Google's "verify it's you" step-up), pause and notify the user to clear
    # it in a visible window rather than silently failing. Default ON — failing
    # loud beats stalling.
    "browser_pause_on_verification": True,
    # Harness UI rework S2 #470 — plugin names the user has disabled.
    # Disabled plugins are scanned + metadata-recorded but not registered.
    "disabled_plugins": [],
    # Skills subsystem S2 #538 (ADR-0014) — skill names the user has enabled.
    # The INVERSE of disabled_plugins: skills are disabled-by-default, so this
    # is an opt-in list. Only enabled skills are visible to the planner.
    "enabled_skills": [],
    # ADR-0016 amendment 2026-08-02 (computer_use background actuation,
    # #592) — master switch. On (default): click_element/type_into try a
    # UIA control pattern (Invoke/Toggle/Select/ExpandCollapse/SetValue)
    # before the pyautogui foreground fallback, so the user keeps the mouse
    # and keyboard while Felix drives. Off: pre-amendment pure-foreground
    # behaviour.
    "background_actuation": True,
    # ADR-0016 amendment — UIA control-type allowlist SetValue may fill.
    # SetValue fills, never submits, and only outside a browser/Electron
    # surface (checked separately by control class, not this list). Empty
    # list keeps pattern clicks background but forces all text through
    # foreground typing.
    "setvalue_roles": ["Edit", "Document"],
    # ADR-0016 amendment (d), #593 -- "what I'm doing takes priority". Below
    # this many ms since the user's last physical input, Felix treats them
    # as PRESENT and will not grab the mouse/keyboard for the foreground
    # (pyautogui) fallback; it waits for idle or hands off to the human
    # instead. Ignored while the full-autonomy switch is on. ADR range:
    # 3000-5000ms.
    "user_idle_ms": 4000,
    # S11 Part 2: master arm/disarm toggle for live trading. Default OFF.
    # Independently readable/writable. Required alongside strategy
    # graduation to enable real-money orders; credentials or lifecycle
    # status alone are insufficient.
    "trading_live_arm": False,
    # S20: Risk limits for live trading. Readable/writable via SettingsStore.
    "max_per_trade_risk_pct":    2.0,
    "max_daily_loss_pct":        6.0,
    "max_concurrent_positions":  10,
}

_VALID_KEYS: frozenset[str] = frozenset(_DEFAULTS)

# Expected Python type for each setting key.
_TYPES: dict[str, type] = {
    "notifications_enabled":     bool,
    "reminder_interval_minutes": int,
    "camera_enabled":            bool,
    "visualiser_visible":        bool,
    "mic_mode":                  str,
    "ptt_key":                   str,
    "tts_muted":                 bool,
    "tts_volume":                int,
    "mic_input_device":          str,
    "browser_pause_on_verification": bool,
    "disabled_plugins":          list,
    "enabled_skills":            list,
    "background_actuation":      bool,
    "setvalue_roles":            list,
    "user_idle_ms":              int,
    "trading_live_arm":          bool,
    "max_per_trade_risk_pct":    float,
    "max_daily_loss_pct":        float,
    "max_concurrent_positions":  int,
}

_MIC_MODE_VALUES: frozenset[str] = frozenset({"passive", "ptt", "disabled"})

from cerebral.paths import data_dir

_SETTINGS_PATH = data_dir() / "felix-settings.json"


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
        if key == "user_idle_ms":
            value = max(0, value)
        if key == "mic_mode" and value not in _MIC_MODE_VALUES:
            raise ValueError(
                f"mic_mode must be one of {sorted(_MIC_MODE_VALUES)}, got {value!r}"
            )
        if key == "disabled_plugins" and not all(isinstance(i, str) for i in value):
            raise ValueError("disabled_plugins must be a list of str")
        if key == "enabled_skills" and not all(isinstance(i, str) for i in value):
            raise ValueError("enabled_skills must be a list of str")
        if key == "setvalue_roles" and not all(isinstance(i, str) for i in value):
            raise ValueError("setvalue_roles must be a list of str")
        self._data[key] = value
        self._save()

    def all(self) -> dict[str, Any]:
        """Return a snapshot of all settings."""
        return {k: self._data.get(k, v) for k, v in _DEFAULTS.items()}

    def is_default(self, key: str) -> bool:
        """Return True when the current value equals its shipped default."""
        return self.get(key) == _DEFAULTS.get(key)

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
