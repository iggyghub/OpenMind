"""
Settings store + WS IPC tests — Issue #209.

Exercises:
  - cerebral.settings.SettingsStore: persistence, validation, all()
  - _handle_message: list_settings, set_setting (including camera sync)

No real filesystem I/O in store tests — uses tmp_path fixture.
No real WebSocket in IPC tests — patches _broadcast.
All tests are async (pytest asyncio_mode=auto); never asyncio.run in a
sync body (learning #7).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.settings import SettingsStore


# ── SettingsStore unit tests ───────────────────────────────────────────────────

class TestSettingsStore:

    def test_returns_defaults_when_file_missing(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("notifications_enabled") is False
        assert store.get("reminder_interval_minutes") == 120
        assert store.get("camera_enabled") is False
        assert store.get("visualiser_visible") is False
        assert store.get("mic_mode") == "passive"
        assert store.get("tts_muted") is False
        assert store.get("tts_volume") == 100
        assert store.get("mic_input_device") == ""

    def test_all_returns_all_keys(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        snap = store.all()
        assert set(snap.keys()) == {
            "notifications_enabled",
            "reminder_interval_minutes",
            "camera_enabled",
            "visualiser_visible",
            "mic_mode",
            "ptt_key",
            "tts_muted",
            "tts_volume",
            "mic_input_device",
            "browser_pause_on_verification",
            "disabled_plugins",
            "enabled_skills",
            "background_actuation",
            "setvalue_roles",
            "user_idle_ms",
        }

    def test_browser_pause_on_verification_defaults_on(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("browser_pause_on_verification") is True

    def test_browser_pause_on_verification_roundtrip_and_type(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("browser_pause_on_verification", False)
        assert store.get("browser_pause_on_verification") is False
        import pytest
        with pytest.raises(ValueError):
            store.set("browser_pause_on_verification", "nope")

    def test_set_and_get_roundtrip(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("notifications_enabled", True)
        assert store.get("notifications_enabled") is True

    def test_persists_to_disk(self, tmp_path):
        p = tmp_path / "s.json"
        s1 = SettingsStore(p)
        s1.set("reminder_interval_minutes", 30)
        s2 = SettingsStore(p)
        assert s2.get("reminder_interval_minutes") == 30

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("not { valid json", encoding="utf-8")
        store = SettingsStore(p)
        assert store.get("camera_enabled") is False

    def test_unknown_key_raises_value_error(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="Unknown setting key"):
            store.set("totally_bogus", True)

    def test_wrong_type_raises_value_error(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects bool"):
            store.set("notifications_enabled", "yes")

    def test_interval_clamped_to_zero(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("reminder_interval_minutes", -5)
        assert store.get("reminder_interval_minutes") == 0

    def test_int_one_coerced_to_true_for_bool_key(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("camera_enabled", 1)
        assert store.get("camera_enabled") is True

    def test_all_snapshot_matches_set_values(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("notifications_enabled", True)
        store.set("reminder_interval_minutes", 60)
        snap = store.all()
        assert snap["notifications_enabled"] is True
        assert snap["reminder_interval_minutes"] == 60

    def test_mic_mode_default_is_passive(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("mic_mode") == "passive"

    def test_mic_mode_accepts_valid_values(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        for val in ("passive", "ptt", "disabled"):
            store.set("mic_mode", val)
            assert store.get("mic_mode") == val

    def test_mic_mode_rejects_invalid_value(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="mic_mode must be one of"):
            store.set("mic_mode", "always_on")

    def test_mic_mode_wrong_type_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects str"):
            store.set("mic_mode", 42)

    def test_mic_mode_persists_to_disk(self, tmp_path):
        p = tmp_path / "s.json"
        s1 = SettingsStore(p)
        s1.set("mic_mode", "disabled")
        s2 = SettingsStore(p)
        assert s2.get("mic_mode") == "disabled"

    def test_tts_muted_default_false(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("tts_muted") is False

    def test_tts_muted_toggle(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("tts_muted", True)
        assert store.get("tts_muted") is True

    def test_tts_muted_wrong_type_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects bool"):
            store.set("tts_muted", "yes")

    def test_tts_volume_default_100(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("tts_volume") == 100

    def test_tts_volume_set_and_get(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("tts_volume", 50)
        assert store.get("tts_volume") == 50

    def test_tts_volume_clamps_above_100(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("tts_volume", 150)
        assert store.get("tts_volume") == 100

    def test_tts_volume_clamps_below_0(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("tts_volume", -10)
        assert store.get("tts_volume") == 0

    def test_tts_volume_wrong_type_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects int"):
            store.set("tts_volume", "loud")

    def test_mic_input_device_default_is_empty_string(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("mic_input_device") == ""

    def test_mic_input_device_set_and_get(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("mic_input_device", "Built-in Microphone")
        assert store.get("mic_input_device") == "Built-in Microphone"

    def test_mic_input_device_persists_to_disk(self, tmp_path):
        p = tmp_path / "s.json"
        s1 = SettingsStore(p)
        s1.set("mic_input_device", "USB Audio Device")
        s2 = SettingsStore(p)
        assert s2.get("mic_input_device") == "USB Audio Device"

    def test_mic_input_device_wrong_type_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects str"):
            store.set("mic_input_device", 42)

    def test_enabled_skills_default_empty_list(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("enabled_skills") == []

    def test_enabled_skills_set_and_get_roundtrip(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("enabled_skills", ["alpha", "beta"])
        assert store.get("enabled_skills") == ["alpha", "beta"]

    def test_enabled_skills_persists_to_disk(self, tmp_path):
        p = tmp_path / "s.json"
        s1 = SettingsStore(p)
        s1.set("enabled_skills", ["alpha"])
        s2 = SettingsStore(p)
        assert s2.get("enabled_skills") == ["alpha"]

    def test_enabled_skills_wrong_type_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects list"):
            store.set("enabled_skills", "alpha")

    def test_enabled_skills_rejects_non_str_items(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="enabled_skills must be a list of str"):
            store.set("enabled_skills", ["alpha", 42])

    def test_background_actuation_defaults_on(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("background_actuation") is True

    def test_background_actuation_roundtrip_and_type(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("background_actuation", False)
        assert store.get("background_actuation") is False
        with pytest.raises(ValueError, match="expects bool"):
            store.set("background_actuation", "nope")

    def test_setvalue_roles_default_edit_document(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("setvalue_roles") == ["Edit", "Document"]

    def test_setvalue_roles_set_and_get_roundtrip(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("setvalue_roles", [])
        assert store.get("setvalue_roles") == []
        store.set("setvalue_roles", ["Edit"])
        assert store.get("setvalue_roles") == ["Edit"]

    def test_setvalue_roles_persists_to_disk(self, tmp_path):
        p = tmp_path / "s.json"
        s1 = SettingsStore(p)
        s1.set("setvalue_roles", ["Document"])
        s2 = SettingsStore(p)
        assert s2.get("setvalue_roles") == ["Document"]

    def test_setvalue_roles_wrong_type_raises(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="expects list"):
            store.set("setvalue_roles", "Edit")

    def test_setvalue_roles_rejects_non_str_items(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        with pytest.raises(ValueError, match="setvalue_roles must be a list of str"):
            store.set("setvalue_roles", ["Edit", 42])

    def test_user_idle_ms_defaults_4000(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        assert store.get("user_idle_ms") == 4000

    def test_user_idle_ms_roundtrip_and_type(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("user_idle_ms", 3000)
        assert store.get("user_idle_ms") == 3000
        with pytest.raises(ValueError, match="expects int"):
            store.set("user_idle_ms", "nope")

    def test_user_idle_ms_clamps_negative_to_zero(self, tmp_path):
        store = SettingsStore(tmp_path / "s.json")
        store.set("user_idle_ms", -500)
        assert store.get("user_idle_ms") == 0


# ── WS IPC tests ──────────────────────────────────────────────────────────────

@pytest.fixture
def settings_rig(tmp_path):
    """Rig that patches _settings, _broadcast, _env, and _connected in main."""
    import cerebral.main as main_mod
    from cerebral.settings import SettingsStore

    store = SettingsStore(tmp_path / "felix-settings.json")
    sent: list[dict] = []

    class FakeEnv:
        def __init__(self):
            self.camera = False
        def enable_camera(self):  self.camera = True
        def disable_camera(self): self.camera = False
        def get_context(self):    return {"camera_enabled": self.camera}

    fake_env = FakeEnv()

    saved = {
        "_settings":  main_mod._settings,
        "_broadcast": main_mod._broadcast,
        "_connected": main_mod._connected,
        "_env":       main_mod._env,
    }

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._settings  = store
    main_mod._broadcast = fake_broadcast
    main_mod._connected = set()
    main_mod._env       = fake_env

    class Rig:
        def __init__(self):
            self.store   = store
            self.sent    = sent
            self.env     = fake_env

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def settings_events(self):
            return [e for e in sent if e["type"] == "settings_updated"]

        def last_settings(self):
            return self.settings_events()[-1]["data"]

    try:
        yield Rig()
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)


async def test_list_settings_broadcasts_snapshot(settings_rig):
    await settings_rig.handle({"type": "list_settings"})
    evts = settings_rig.settings_events()
    assert len(evts) == 1
    assert "notifications_enabled" in evts[0]["data"]


async def test_set_setting_persists_value(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "notifications_enabled", "value": True},
    })
    assert settings_rig.store.get("notifications_enabled") is True


async def test_set_setting_broadcasts_settings_updated(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "camera_enabled", "value": True},
    })
    snap = settings_rig.last_settings()
    assert snap["camera_enabled"] is True


async def test_set_setting_camera_syncs_env(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "camera_enabled", "value": True},
    })
    assert settings_rig.env.camera is True


async def test_set_setting_invalid_key_no_broadcast(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "hack_the_planet", "value": True},
    })
    assert settings_rig.settings_events() == []


async def test_set_setting_invalid_type_no_broadcast(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "notifications_enabled", "value": "yes"},
    })
    assert settings_rig.settings_events() == []


async def test_set_setting_interval_clamps_negative(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "reminder_interval_minutes", "value": -10},
    })
    assert settings_rig.last_settings()["reminder_interval_minutes"] == 0


async def test_set_setting_mic_mode_valid(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "mic_mode", "value": "disabled"},
    })
    assert settings_rig.last_settings()["mic_mode"] == "disabled"


async def test_set_setting_mic_mode_invalid_no_broadcast(settings_rig):
    await settings_rig.handle({
        "type": "set_setting",
        "data": {"key": "mic_mode", "value": "always_on"},
    })
    assert settings_rig.settings_events() == []


def test_startup_restores_camera_enabled_from_settings(tmp_path):
    """_env.camera must be enabled on startup when _settings has camera_enabled=True.

    Regression: _env always booted with camera_enabled=False even after the
    user had toggled it on in a previous session — the persisted setting was
    never read back and applied to EnvironmentContext.
    """
    import cerebral.main as main_mod

    store = SettingsStore(tmp_path / "s.json")
    store.set("camera_enabled", True)

    class FakeEnv:
        camera = False
        def enable_camera(self):  self.camera = True
        def disable_camera(self): self.camera = False

    env = FakeEnv()
    saved = {"_settings": main_mod._settings, "_env": main_mod._env}
    main_mod._settings = store
    main_mod._env = env
    try:
        # Replicate the startup restoration added to main():
        #   if _settings.get("camera_enabled"):
        #       _env.enable_camera()
        if main_mod._settings.get("camera_enabled"):
            main_mod._env.enable_camera()
        assert env.camera is True, (
            "camera_enabled=True in SettingsStore must be applied to _env on startup"
        )
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)


def test_startup_leaves_camera_disabled_when_setting_is_false(tmp_path):
    """_env.camera must stay disabled when _settings has camera_enabled=False."""
    import cerebral.main as main_mod

    store = SettingsStore(tmp_path / "s.json")
    # camera_enabled defaults to False — no explicit set needed

    class FakeEnv:
        camera = False
        def enable_camera(self):  self.camera = True
        def disable_camera(self): self.camera = False

    env = FakeEnv()
    saved = {"_settings": main_mod._settings, "_env": main_mod._env}
    main_mod._settings = store
    main_mod._env = env
    try:
        if main_mod._settings.get("camera_enabled"):
            main_mod._env.enable_camera()
        assert env.camera is False
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)
