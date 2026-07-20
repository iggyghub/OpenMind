"""
Plugin settings IPC tests -- Issue #187 (Slice 6).

Covers:
  - _plugins_list_event() includes tool_count + status fields
  - get_plugin_settings broadcasts plugin_settings for discord_user
  - get_plugin_settings with missing plugin_name is a no-op
  - get_plugin_settings for unknown plugin returns empty allowlist
  - get_plugin_settings with no active profile returns empty allowlist
  - discord_allowlist_add adds a row and broadcasts plugin_settings
  - discord_allowlist_add missing sender_id is a no-op
  - discord_allowlist_add with no active profile is a no-op
  - discord_allowlist_remove removes a row and broadcasts plugin_settings
  - discord_allowlist_remove missing sender_id is a no-op
  - discord_allowlist_remove with no active profile is a no-op
  - discord_allowlist_remove non-existent sender still broadcasts

All tests are async (pytest asyncio_mode=auto).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager


def _seed_db(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.executescript(
        "PRAGMA foreign_keys=ON;"
        "CREATE TABLE profiles ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  name TEXT NOT NULL DEFAULT '',"
        "  wake_name TEXT NOT NULL DEFAULT 'felix',"
        "  pronunciation_guide TEXT NOT NULL DEFAULT '',"
        "  voice_id TEXT NOT NULL DEFAULT 'af_heart',"
        "  connected_accounts TEXT NOT NULL DEFAULT '{}',"
        "  voice_sample TEXT NOT NULL DEFAULT '',"
        "  wake_sample TEXT NOT NULL DEFAULT '',"
        "  active_model TEXT NOT NULL DEFAULT '',"
        "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "  last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ");"
        "INSERT INTO profiles (id, name) VALUES (1, 'Test');"
    )
    con.commit()
    con.close()


@pytest.fixture
def ipc_rig(tmp_path, monkeypatch):
    """Patches _pm, _orc, _active_profile, and _broadcast in cerebral.main.

    Stubs _orc with a minimal stand-in so we can control tool_count without
    loading the full plugin set. Stubs _pm with a real ProfileManager backed
    by a temp database so allowlist CRUD is exercised against real SQLite.
    """
    import cerebral.main as main_mod

    db = tmp_path / "openmind.db"
    _seed_db(db)
    pm = ProfileManager(db_path=db)

    profile = Profile(name="Test", id=1)
    sent: list[dict] = []

    async def fake_broadcast(event: dict) -> None:
        sent.append(event)

    class FakeOrchestrator:
        """Minimal orchestrator stub sufficient for _plugins_list_event()."""

        def __init__(self) -> None:
            # Two fake plugins with one tool each.
            self._plugins = {"alpha": object(), "discord_user": object()}
            self._tool_index = {
                "alpha_tool": "alpha",
                "discord_list_conversations": "discord_user",
                "discord_get_messages": "discord_user",
                "discord_send_message": "discord_user",
                "discord_set_presence": "discord_user",
            }
            self.registration_errors: list[dict] = []

        def required_capabilities_for(self, plugin_name: str):
            if plugin_name == "discord_user":
                return frozenset({"secrets_read", "network_egress_cloud"})
            return frozenset()

        def inspectability_for(self, plugin_name: str) -> str:
            return "inspected"

        def registration_tool_count_for(self, plugin_name: str) -> int:
            return sum(1 for v in self._tool_index.values() if v == plugin_name)

    fake_orc = FakeOrchestrator()

    saved = {
        "_pm":              main_mod._pm,
        "_orc":             main_mod._orc,
        "_active_profile":  main_mod._active_profile,
        "_broadcast":       main_mod._broadcast,
        "_connected":       main_mod._connected,
    }
    monkeypatch.setattr(main_mod, "_pm",             pm)
    monkeypatch.setattr(main_mod, "_orc",            fake_orc)
    monkeypatch.setattr(main_mod, "_active_profile", profile)
    monkeypatch.setattr(main_mod, "_broadcast",      fake_broadcast)
    monkeypatch.setattr(main_mod, "_connected",      set())

    # get_plugin_new_flag may not exist on a fresh DB — stub it.
    if not hasattr(pm, "get_plugin_new_flag"):
        monkeypatch.setattr(pm, "get_plugin_new_flag", lambda name: False)

    class Rig:
        def __init__(self) -> None:
            self.pm = pm
            self.sent = sent
            self.profile = profile
            self.orc = fake_orc

        async def handle(self, msg: dict) -> None:
            await main_mod._handle_message(msg)

        def settings_events(self) -> list[dict]:
            return [e for e in sent if e["type"] == "plugin_settings"]

        def plugins_list_events(self) -> list[dict]:
            return [e for e in sent if e["type"] == "plugins_list"]

    yield Rig()


# ---------------------------------------------------------------------------
# _plugins_list_event: tool_count + status
# ---------------------------------------------------------------------------

async def test_plugins_list_includes_tool_count(ipc_rig):
    await ipc_rig.handle({"type": "list_plugins"})
    evts = ipc_rig.plugins_list_events()
    assert len(evts) == 1
    plugins = {p["name"]: p for p in evts[0]["data"]["plugins"]}
    assert plugins["alpha"]["tool_count"] == 1
    assert plugins["discord_user"]["tool_count"] == 4


async def test_plugins_list_includes_status_loaded(ipc_rig):
    await ipc_rig.handle({"type": "list_plugins"})
    evts = ipc_rig.plugins_list_events()
    plugins = evts[0]["data"]["plugins"]
    for p in plugins:
        assert p["status"] == "loaded"


# ---------------------------------------------------------------------------
# get_plugin_settings
# ---------------------------------------------------------------------------

async def test_get_plugin_settings_discord_user_empty_allowlist(ipc_rig):
    await ipc_rig.handle({
        "type": "get_plugin_settings",
        "data": {"plugin_name": "discord_user"},
    })
    evts = ipc_rig.settings_events()
    assert len(evts) == 1
    data = evts[0]["data"]
    assert data["plugin_name"] == "discord_user"
    assert data["allowlist"] == []


async def test_get_plugin_settings_returns_allowlist_entries(ipc_rig):
    ipc_rig.pm.add_discord_allowlist(1, "111222333", "Alice")
    ipc_rig.pm.add_discord_allowlist(1, "444555666", "")
    await ipc_rig.handle({
        "type": "get_plugin_settings",
        "data": {"plugin_name": "discord_user"},
    })
    evts = ipc_rig.settings_events()
    allowlist = evts[0]["data"]["allowlist"]
    sender_ids = [e["sender_id"] for e in allowlist]
    assert "111222333" in sender_ids
    assert "444555666" in sender_ids
    assert len(allowlist) == 2


async def test_get_plugin_settings_unknown_plugin_returns_empty(ipc_rig):
    await ipc_rig.handle({
        "type": "get_plugin_settings",
        "data": {"plugin_name": "no_such_plugin"},
    })
    evts = ipc_rig.settings_events()
    assert len(evts) == 1
    data = evts[0]["data"]
    assert data["plugin_name"] == "no_such_plugin"
    assert data["allowlist"] == []


async def test_get_plugin_settings_missing_plugin_name_is_noop(ipc_rig):
    await ipc_rig.handle({"type": "get_plugin_settings", "data": {}})
    assert ipc_rig.settings_events() == []


async def test_get_plugin_settings_no_active_profile_returns_empty(ipc_rig, monkeypatch):
    import cerebral.main as main_mod
    monkeypatch.setattr(main_mod, "_active_profile", None)
    await ipc_rig.handle({
        "type": "get_plugin_settings",
        "data": {"plugin_name": "discord_user"},
    })
    evts = ipc_rig.settings_events()
    assert len(evts) == 1
    assert evts[0]["data"]["allowlist"] == []


# ---------------------------------------------------------------------------
# discord_allowlist_add
# ---------------------------------------------------------------------------

async def test_discord_allowlist_add_persists_entry(ipc_rig):
    await ipc_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "123456789", "note": "Bob"},
    })
    rows = ipc_rig.pm.list_discord_allowlist(1)
    assert any(r["sender_id"] == "123456789" and r["note"] == "Bob" for r in rows)


async def test_discord_allowlist_add_broadcasts_plugin_settings(ipc_rig):
    await ipc_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "123456789"},
    })
    evts = ipc_rig.settings_events()
    assert len(evts) == 1
    data = evts[0]["data"]
    assert data["plugin_name"] == "discord_user"
    sender_ids = [e["sender_id"] for e in data["allowlist"]]
    assert "123456789" in sender_ids


async def test_discord_allowlist_add_missing_sender_id_is_noop(ipc_rig):
    await ipc_rig.handle({"type": "discord_allowlist_add", "data": {}})
    assert ipc_rig.settings_events() == []
    assert ipc_rig.pm.list_discord_allowlist(1) == []


async def test_discord_allowlist_add_no_active_profile_is_noop(ipc_rig, monkeypatch):
    import cerebral.main as main_mod
    monkeypatch.setattr(main_mod, "_active_profile", None)
    await ipc_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "123456789"},
    })
    assert ipc_rig.settings_events() == []
    assert ipc_rig.pm.list_discord_allowlist(1) == []


async def test_discord_allowlist_add_no_note_defaults_empty(ipc_rig):
    await ipc_rig.handle({
        "type": "discord_allowlist_add",
        "data": {"sender_id": "999", "note": ""},
    })
    rows = ipc_rig.pm.list_discord_allowlist(1)
    assert rows[0]["note"] == ""


# ---------------------------------------------------------------------------
# discord_allowlist_remove
# ---------------------------------------------------------------------------

async def test_discord_allowlist_remove_deletes_entry(ipc_rig):
    ipc_rig.pm.add_discord_allowlist(1, "123456789", "")
    await ipc_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "123456789"},
    })
    rows = ipc_rig.pm.list_discord_allowlist(1)
    assert not any(r["sender_id"] == "123456789" for r in rows)


async def test_discord_allowlist_remove_broadcasts_plugin_settings(ipc_rig):
    ipc_rig.pm.add_discord_allowlist(1, "123456789", "")
    await ipc_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "123456789"},
    })
    evts = ipc_rig.settings_events()
    assert len(evts) == 1
    data = evts[0]["data"]
    assert data["plugin_name"] == "discord_user"
    # Entry should be gone from the broadcast
    assert not any(e["sender_id"] == "123456789" for e in data["allowlist"])


async def test_discord_allowlist_remove_missing_sender_id_is_noop(ipc_rig):
    await ipc_rig.handle({"type": "discord_allowlist_remove", "data": {}})
    assert ipc_rig.settings_events() == []


async def test_discord_allowlist_remove_no_active_profile_is_noop(ipc_rig, monkeypatch):
    import cerebral.main as main_mod
    monkeypatch.setattr(main_mod, "_active_profile", None)
    ipc_rig.pm.add_discord_allowlist(1, "123456789", "")
    await ipc_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "123456789"},
    })
    assert ipc_rig.settings_events() == []
    # Entry should still be in DB since the handler was a no-op
    rows = ipc_rig.pm.list_discord_allowlist(1)
    assert any(r["sender_id"] == "123456789" for r in rows)


async def test_discord_allowlist_remove_nonexistent_still_broadcasts(ipc_rig):
    await ipc_rig.handle({
        "type": "discord_allowlist_remove",
        "data": {"sender_id": "does_not_exist"},
    })
    evts = ipc_rig.settings_events()
    assert len(evts) == 1
    assert evts[0]["data"]["plugin_name"] == "discord_user"


# ---------------------------------------------------------------------------
# S2 #470 -- disabled_plugins setting + plugins:set_enabled
# ---------------------------------------------------------------------------

# Minimal plugin source strings used to create real temp plugin files.
_ALPHA_SRC = """\
PLUGIN_NAME = "alpha_p"
REQUIRED_CAPABILITIES = frozenset()
from cerebral.mcp.orchestrator import Tool, ToolResult
class _P:
    name = "alpha_p"
    def list_tools(self):
        return [Tool(name="shared_tool", description="from alpha", plugin="alpha_p")]
    async def call_tool(self, n, a): return ToolResult(content="ok")
def create(): return _P()
"""

# beta supersedes shared_tool from alpha
_BETA_SRC = """\
PLUGIN_NAME = "beta_p"
REQUIRED_CAPABILITIES = frozenset()
from cerebral.mcp.orchestrator import Tool, ToolResult
class _P:
    name = "beta_p"
    def list_tools(self):
        return [Tool(name="shared_tool", description="from beta", plugin="beta_p")]
    async def call_tool(self, n, a): return ToolResult(content="ok2")
def create(): return _P()
"""


@pytest.fixture
def s2_rig(tmp_path, monkeypatch):
    """Fixture for S2 tests using a real MCPOrchestrator + real temp plugin files."""
    from cerebral.mcp.orchestrator import MCPOrchestrator
    from cerebral.settings import SettingsStore
    import cerebral.main as main_mod

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "alpha_p.py").write_text(_ALPHA_SRC, encoding="utf-8")
    (plugins_dir / "beta_p.py").write_text(_BETA_SRC, encoding="utf-8")

    settings_path = tmp_path / "settings.json"
    st = SettingsStore(path=settings_path)
    orc = MCPOrchestrator()
    orc.discover_plugins(plugins_dir, disabled=frozenset())

    sent: list[dict] = []

    async def fake_broadcast(event: dict) -> None:
        sent.append(event)

    monkeypatch.setattr(main_mod, "_orc", orc)
    monkeypatch.setattr(main_mod, "_settings", st)
    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_connected", set())
    monkeypatch.setattr(main_mod, "_active_profile", None)

    class Rig:
        def __init__(self):
            self.orc = orc
            self.st = st
            self.sent = sent
            self.plugins_dir = plugins_dir

        async def handle(self, msg: dict) -> None:
            await main_mod._handle_message(msg)

        def plugins_changed_events(self):
            return [e for e in sent if e["type"] == "plugins:changed"]

        def error_events(self):
            return [e for e in sent if e["type"] == "error"]

        def snapshot_plugins(self):
            evts = [e for e in sent if e["type"] == "plugins:list"]
            return {p["name"]: p for p in evts[-1]["data"]["plugins"]} if evts else {}

    yield Rig()


async def test_disable_removes_tool_from_index(s2_rig):
    assert "shared_tool" in s2_rig.orc._tool_index
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "beta_p", "enabled": False},
    })
    # beta_p was the active owner of shared_tool; unregistering it should
    # restore alpha_p as the owner (takeover-revert).
    assert s2_rig.orc._tool_index.get("shared_tool") == "alpha_p"
    assert "beta_p" not in s2_rig.orc._plugins


async def test_disable_takeover_plugin_reverts_to_original_owner(s2_rig):
    # beta registered after alpha, so beta owns shared_tool.
    assert s2_rig.orc._tool_index["shared_tool"] == "beta_p"
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "beta_p", "enabled": False},
    })
    assert s2_rig.orc._tool_index["shared_tool"] == "alpha_p"


async def test_disabled_plugin_appears_in_snapshot_with_metadata(s2_rig):
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": False},
    })
    snap = s2_rig.snapshot_plugins()
    assert "alpha_p" in snap
    assert snap["alpha_p"]["status"] == "disabled"
    assert snap["alpha_p"]["enabled"] is False
    # Card must still carry tool metadata
    tool_names = [t["name"] for t in snap["alpha_p"]["tools"]]
    assert "shared_tool" in tool_names


async def test_enable_reregisters_plugin(s2_rig):
    # Disable first
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": False},
    })
    assert "alpha_p" not in s2_rig.orc._plugins
    # Re-enable
    s2_rig.sent.clear()
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": True},
    })
    assert "alpha_p" in s2_rig.orc._plugins
    snap = s2_rig.snapshot_plugins()
    assert snap["alpha_p"]["status"] == "active"
    assert snap["alpha_p"]["enabled"] is True


async def test_unknown_plugin_name_returns_error(s2_rig):
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "no_such_plugin", "enabled": False},
    })
    errs = s2_rig.error_events()
    assert len(errs) == 1
    assert "no_such_plugin" in errs[0]["data"]["message"]


async def test_enable_missing_file_moves_to_errors(s2_rig):
    # Disable alpha_p
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": False},
    })
    # Delete the plugin file
    (s2_rig.plugins_dir / "alpha_p.py").unlink()
    s2_rig.sent.clear()
    # Attempt to re-enable
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": True},
    })
    assert "alpha_p" not in s2_rig.orc._plugins
    error_reasons = [e["reason"] for e in s2_rig.orc.registration_errors]
    assert "REASON_FILE_MISSING_ON_ENABLE" in error_reasons


async def test_set_enabled_updates_disabled_plugins_setting(s2_rig):
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": False},
    })
    assert "alpha_p" in s2_rig.st.get("disabled_plugins")
    # Re-enable removes it
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": True},
    })
    assert "alpha_p" not in s2_rig.st.get("disabled_plugins")


async def test_no_secret_in_plugins_list_payload(s2_rig):
    """SAFETY #2: no secret pattern in any plugins:set_enabled response."""
    import re
    import json
    await s2_rig.handle({
        "type": "plugins:set_enabled",
        "data": {"plugin_name": "alpha_p", "enabled": False},
    })
    raw = json.dumps(s2_rig.sent)
    # Secret patterns: long hex/base64 tokens, Bearer tokens, passwords
    assert not re.search(r'"(password|secret|token|key)":\s*"[^"]{8,}"', raw)
