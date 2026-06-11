"""
Discord allowlist + settings WebSocket IPC tests — Issue #187.

Exercises the five new dispatcher branches:
  discord_allowlist_list / discord_allowlist_add / discord_allowlist_remove /
  discord_settings_set / discord_settings_clear

No network, no Discord bot, no production DB: a real ProfileManager on a
tmp_path SQLite file is injected directly into cerebral.main via module-level
monkey-patching — the same pattern used by test_credentials_ipc.py.

Tests are ``async`` (pytest.ini asyncio_mode=auto) — never asyncio.run in a
sync body (learning #7).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager


class _Profile:
    def __init__(self, pid: int) -> None:
        self.id = pid


@pytest.fixture
def discord_rig(tmp_path):
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "openmind.db")
    real_profile = pm.create(name="TestUser", wake_name="felix", voice_id="af_heart")
    profile = _Profile(real_profile.id)

    saved = {
        "_active_profile": main_mod._active_profile,
        "_broadcast": main_mod._broadcast,
        "_connected": main_mod._connected,
        "_pm": main_mod._pm,
    }

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._active_profile = profile
    main_mod._broadcast = fake_broadcast
    main_mod._connected = set()
    main_mod._pm = pm

    class Rig:
        def __init__(self):
            self.pm = pm
            self.profile = profile
            self.sent = sent
            self.module = main_mod

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def states(self):
            return [e for e in sent if e["type"] == "discord_state"]

        def last(self):
            return self.states()[-1]["data"]

        def no_profile(self):
            main_mod._active_profile = None

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── discord_allowlist_list ────────────────────────────────────────────────────

async def test_list_empty_on_fresh_profile(discord_rig):
    await discord_rig.handle({"type": "discord_allowlist_list"})
    d = discord_rig.last()
    assert d["allowlist"] == []
    assert d["profile_id"] == discord_rig.profile.id


async def test_list_no_profile_returns_empty_snapshot(discord_rig):
    discord_rig.no_profile()
    await discord_rig.handle({"type": "discord_allowlist_list"})
    d = discord_rig.last()
    assert d["profile_id"] is None
    assert d["allowlist"] == []
    assert d["settings"] == {}


# ── discord_allowlist_add ─────────────────────────────────────────────────────

async def test_add_persists_sender(discord_rig):
    await discord_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "123456789", "note": "my friend"},
    })
    rows = discord_rig.pm.list_discord_allowlist(discord_rig.profile.id)
    assert len(rows) == 1
    assert rows[0]["sender_id"] == "123456789"
    assert rows[0]["note"] == "my friend"


async def test_add_broadcasts_discord_state(discord_rig):
    await discord_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "111", "note": ""},
    })
    assert len(discord_rig.states()) == 1
    d = discord_rig.last()
    assert len(d["allowlist"]) == 1
    assert d["allowlist"][0]["sender_id"] == "111"


async def test_add_missing_sender_id_no_op(discord_rig):
    await discord_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "   ", "note": "whatever"},
    })
    assert discord_rig.states() == []
    assert discord_rig.pm.list_discord_allowlist(discord_rig.profile.id) == []


async def test_add_no_profile_no_op(discord_rig):
    discord_rig.no_profile()
    await discord_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "999", "note": ""},
    })
    assert discord_rig.states() == []


async def test_add_idempotent_updates_note(discord_rig):
    await discord_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "42", "note": "first"},
    })
    await discord_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "42", "note": "updated"},
    })
    rows = discord_rig.pm.list_discord_allowlist(discord_rig.profile.id)
    assert len(rows) == 1
    assert rows[0]["note"] == "updated"


# ── discord_allowlist_remove ──────────────────────────────────────────────────

async def test_remove_deletes_existing_row(discord_rig):
    discord_rig.pm.add_discord_allowlist(discord_rig.profile.id, "555", "test")
    await discord_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "555"},
    })
    assert discord_rig.pm.list_discord_allowlist(discord_rig.profile.id) == []


async def test_remove_broadcasts_updated_state(discord_rig):
    discord_rig.pm.add_discord_allowlist(discord_rig.profile.id, "777")
    await discord_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "777"},
    })
    d = discord_rig.last()
    assert d["allowlist"] == []


async def test_remove_nonexistent_is_no_error(discord_rig):
    await discord_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "nonexistent"},
    })
    # Still broadcasts state (idempotent remove is silent success).
    assert len(discord_rig.states()) == 1


async def test_remove_missing_sender_id_no_op(discord_rig):
    await discord_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": ""},
    })
    assert discord_rig.states() == []


async def test_remove_no_profile_no_op(discord_rig):
    discord_rig.no_profile()
    await discord_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "123"},
    })
    assert discord_rig.states() == []


# ── discord_settings_set ──────────────────────────────────────────────────────

async def test_settings_set_persists_key_value(discord_rig):
    await discord_rig.handle({
        "type": "discord_settings_set",
        "data": {"key": "delay_min_s", "value": "3.0"},
    })
    result = discord_rig.pm.get_discord_setting(discord_rig.profile.id, "delay_min_s")
    assert result == "3.0"


async def test_settings_set_broadcasts_state(discord_rig):
    await discord_rig.handle({
        "type": "discord_settings_set",
        "data": {"key": "typing_indicator", "value": "0"},
    })
    d = discord_rig.last()
    assert d["settings"]["typing_indicator"] == "0"


async def test_settings_set_missing_key_no_op(discord_rig):
    await discord_rig.handle({
        "type": "discord_settings_set",
        "data": {"key": "", "value": "5"},
    })
    assert discord_rig.states() == []


async def test_settings_set_no_profile_no_op(discord_rig):
    discord_rig.no_profile()
    await discord_rig.handle({
        "type": "discord_settings_set",
        "data": {"key": "delay_max_s", "value": "10"},
    })
    assert discord_rig.states() == []


async def test_settings_set_multiple_keys_accumulate(discord_rig):
    for key, val in [("delay_min_s", "1"), ("delay_max_s", "20"), ("rate_limit_max", "5")]:
        await discord_rig.handle({
            "type": "discord_settings_set",
            "data": {"key": key, "value": val},
        })
    d = discord_rig.last()
    assert d["settings"]["delay_min_s"] == "1"
    assert d["settings"]["delay_max_s"] == "20"
    assert d["settings"]["rate_limit_max"] == "5"


# ── discord_settings_clear ────────────────────────────────────────────────────

async def test_settings_clear_removes_key(discord_rig):
    discord_rig.pm.set_discord_setting(discord_rig.profile.id, "delay_min_s", "2")
    await discord_rig.handle({
        "type": "discord_settings_clear",
        "data": {"key": "delay_min_s"},
    })
    assert discord_rig.pm.get_discord_setting(discord_rig.profile.id, "delay_min_s") is None


async def test_settings_clear_broadcasts_updated_state(discord_rig):
    discord_rig.pm.set_discord_setting(discord_rig.profile.id, "typing_indicator", "1")
    await discord_rig.handle({
        "type": "discord_settings_clear",
        "data": {"key": "typing_indicator"},
    })
    d = discord_rig.last()
    assert "typing_indicator" not in d["settings"]


async def test_settings_clear_nonexistent_key_broadcasts_state(discord_rig):
    await discord_rig.handle({
        "type": "discord_settings_clear",
        "data": {"key": "nonexistent_key"},
    })
    assert len(discord_rig.states()) == 1


async def test_settings_clear_missing_key_no_op(discord_rig):
    await discord_rig.handle({
        "type": "discord_settings_clear",
        "data": {"key": ""},
    })
    assert discord_rig.states() == []


async def test_settings_clear_no_profile_no_op(discord_rig):
    discord_rig.no_profile()
    await discord_rig.handle({
        "type": "discord_settings_clear",
        "data": {"key": "delay_min_s"},
    })
    assert discord_rig.states() == []


# ── combined allowlist + settings in state payload ────────────────────────────

async def test_state_payload_includes_both_allowlist_and_settings(discord_rig):
    discord_rig.pm.add_discord_allowlist(discord_rig.profile.id, "user1", "note1")
    discord_rig.pm.set_discord_setting(discord_rig.profile.id, "delay_min_s", "3")
    await discord_rig.handle({"type": "discord_allowlist_list"})
    d = discord_rig.last()
    assert len(d["allowlist"]) == 1
    assert d["allowlist"][0]["sender_id"] == "user1"
    assert d["settings"]["delay_min_s"] == "3"
