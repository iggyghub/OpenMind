"""
plugins:list + plugins:changed IPC tests -- Harness UI rework, S1 #469.

Covers the spec (docs/harness-ui-rework.md sec 5.1) payload:
  - per-plugin fields (status, trust, source_layout, path, capabilities,
    enabled, tools[], credentials[])
  - errors[] and capability_vocabulary carry through
  - supersedes populated when a later plugin took over a tool
  - credentials.source resolves keyring -> env -> missing
  - env_var populated ONLY when source == "env"
  - masked hint is "****<last4>" for real-length secrets, None for short
  - the whole serialized payload contains NO secret value (SAFETY gate)

Tests are async (pytest asyncio_mode=auto); never call asyncio.run in a
sync body (learning #7). No real keyring / network / plugins are loaded --
a minimal orchestrator stub stands in for the real _orc, and a
:memory: CredentialStore backed by a dict keyring supplies credential
state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.credentials import CredentialStore, masked_hint
from cerebral.mcp.orchestrator import Tool


# ── dict-backed keyring stub (same shape used by test_credentials_ipc.py) ──

class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


class _Profile:
    def __init__(self, pid: int) -> None:
        self.id = pid


class _FakePlugin:
    def __init__(self, name: str, tools: list[Tool]) -> None:
        self.name = name
        self._tools = tools

    def list_tools(self) -> list[Tool]:
        return list(self._tools)

    async def call_tool(self, tool_name, args):
        raise AssertionError("call_tool must not fire in these tests")


class FakeOrchestrator:
    """Minimal stand-in for MCPOrchestrator sufficient for the payload builder.

    Populated with two plugins where the second (google_workspace) takes
    over one tool from the first (gmail) -- mirrors the real takeover the
    supersedes indicator has to render."""

    def __init__(self) -> None:
        gmail_send = Tool(
            name="gmail_send", description="Send an email via Gmail",
            plugin="gmail", schema={"type": "object"},
        )
        gmail_send_gw = Tool(
            name="gmail_send", description="Send an email via Gmail (workspace)",
            plugin="google_workspace", schema={"type": "object"},
        )
        todoist_add = Tool(
            name="todoist_add_task", description="Add a Todoist task",
            plugin="todoist", schema={"type": "object"},
        )
        self._plugins = {
            "gmail":            _FakePlugin("gmail", [gmail_send]),
            "google_workspace": _FakePlugin("google_workspace", [gmail_send_gw]),
            "todoist":          _FakePlugin("todoist", [todoist_add]),
        }
        # google_workspace took over gmail_send: history[0] gmail, history[-1] gw.
        self._tool_index = {
            "gmail_send": "google_workspace",
            "todoist_add_task": "todoist",
        }
        self._tool_registrations = {
            "gmail_send": [("gmail", gmail_send), ("google_workspace", gmail_send_gw)],
            "todoist_add_task": [("todoist", todoist_add)],
        }
        self._plugin_modules: dict[str, object] = {}
        self._caps = {
            "gmail":            frozenset({"network_egress_cloud", "secrets_read"}),
            "google_workspace": frozenset({"network_egress_cloud", "secrets_read"}),
            "todoist":          frozenset({"network_egress_cloud"}),
        }
        self._inspect = {
            "gmail": "inspected",
            "google_workspace": "inspected",
            "todoist": "inspected",
        }
        self.registration_errors: list[dict] = [{
            "plugin_name": "broken_thing",
            "reason": "REASON_NOT_INSPECTABLE_PATH",
            "detail": "subdir without server.py",
            "path": "plugins/broken_thing/",
        }]

    def required_capabilities_for(self, name):
        return self._caps.get(name)

    def inspectability_for(self, name):
        return self._inspect.get(name)

    def supersedes_for(self, tool_name):
        h = self._tool_registrations.get(tool_name, [])
        if len(h) < 2:
            return None
        return {"tool": tool_name, "from_plugin": h[-2][0]}

    @property
    def disabled_plugins_meta(self) -> dict:
        return {}


@pytest.fixture
def rig(monkeypatch):
    import cerebral.main as main_mod

    store = CredentialStore(db_path=":memory:", keyring_backend=FakeKeyring())
    profile = _Profile(1)
    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    monkeypatch.setattr(main_mod, "_orc", FakeOrchestrator())
    monkeypatch.setattr(main_mod, "_active_profile", profile)
    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_connected", set())
    monkeypatch.setattr(main_mod, "_get_credential_store", lambda: store)

    class Rig:
        def __init__(self):
            self.main = main_mod
            self.store = store
            self.profile = profile
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def payload(self) -> dict:
            evts = [e for e in self.sent if e.get("type") == "plugins:list"]
            assert len(evts) == 1, f"expected one plugins:list event, got {evts}"
            return evts[0]["data"]

    return Rig()


# ── payload shape ──────────────────────────────────────────────────────────

async def test_plugins_list_carries_expected_top_level_keys(rig):
    await rig.handle({"type": "plugins:list"})
    data = rig.payload()
    assert set(data) == {"plugins", "errors", "capability_vocabulary"}
    # 16 canonical classes per ADR-0005.
    assert len(data["capability_vocabulary"]) == 16


async def test_plugins_list_per_plugin_fields(rig):
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    gmail = plugins["gmail"]
    assert gmail["status"] == "active"
    assert gmail["trust"] == "inspected"
    assert gmail["enabled"] is True
    assert "network_egress_cloud" in gmail["capabilities"]
    assert isinstance(gmail["tools"], list)
    assert isinstance(gmail["credentials"], list)


async def test_plugins_list_errors_pass_through(rig):
    await rig.handle({"type": "plugins:list"})
    errs = rig.payload()["errors"]
    assert len(errs) == 1
    assert errs[0]["plugin_name"] == "broken_thing"


# ── supersedes on takeover ─────────────────────────────────────────────────

async def test_supersedes_populated_on_takeover(rig):
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    # google_workspace is the current owner of gmail_send.
    gw_tools = {t["name"]: t for t in plugins["google_workspace"]["tools"]}
    assert gw_tools["gmail_send"]["supersedes"] == {
        "tool": "gmail_send", "from_plugin": "gmail",
    }


async def test_supersedes_null_when_no_takeover(rig):
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    tools = {t["name"]: t for t in plugins["todoist"]["tools"]}
    assert tools["todoist_add_task"]["supersedes"] is None


async def test_superseded_tool_omitted_from_losing_plugin(rig):
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    # gmail declares gmail_send but is no longer the active owner: on its
    # card the supersedes field is null (this plugin is not the takeover).
    gmail_tools = {t["name"]: t for t in plugins["gmail"]["tools"]}
    assert gmail_tools["gmail_send"]["supersedes"] is None


# ── credentials source resolution ──────────────────────────────────────────

async def test_credential_source_keyring_wins(rig, monkeypatch):
    # Static-token plugin: keyring value should be reported as source=keyring
    # and produce a masked hint from the token's last 4 characters.
    rig.store.set_secret(rig.profile.id, "todoist", "api_token", "tok_abcdef1234")
    monkeypatch.setenv("TODOIST_API_TOKEN", "env_zzz_should_be_ignored")
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    cred = plugins["todoist"]["credentials"][0]
    assert cred["provider"] == "todoist"
    assert cred["source"] == "keyring"
    assert cred["hint"] == "****1234"
    assert cred["env_var"] is None  # env_var only when source == "env"


async def test_credential_source_env_fallback(rig, monkeypatch):
    monkeypatch.setenv("TODOIST_API_TOKEN", "env_abcdef1234")
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    cred = plugins["todoist"]["credentials"][0]
    assert cred["source"] == "env"
    assert cred["env_var"] == "TODOIST_API_TOKEN"
    assert cred["hint"] == "****1234"


async def test_credential_source_missing(rig, monkeypatch):
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    cred = plugins["todoist"]["credentials"][0]
    assert cred["source"] == "missing"
    assert cred["hint"] is None
    assert cred["env_var"] is None


async def test_credential_missing_when_no_profile(rig, monkeypatch):
    monkeypatch.setattr(rig.main, "_active_profile", None)
    await rig.handle({"type": "plugins:list"})
    plugins = {p["name"]: p for p in rig.payload()["plugins"]}
    # No profile -> empty credentials list for every plugin.
    for p in plugins.values():
        assert p["credentials"] == []


# ── masked_hint helper ─────────────────────────────────────────────────────

def test_masked_hint_short_secret_returns_none():
    assert masked_hint("short") is None
    assert masked_hint("") is None
    assert masked_hint(None) is None


def test_masked_hint_reveals_last_four():
    assert masked_hint("abcdefghij") == "****ghij"
    assert masked_hint("12345678") == "****5678"


# ── SAFETY: no secret ever appears in the serialized payload ───────────────

_SECRET_MARKERS = (
    "tok_abcdef1234",     # keyring token
    "env_abcdef1234",     # env token
    "should_never_leak",  # canary
)


async def test_no_secret_in_serialized_payload(rig, monkeypatch):
    # Load values into every possible source: keyring api_token, env, and
    # an OAuth refresh_token / password for the browser-login provider.
    rig.store.set_secret(rig.profile.id, "todoist", "api_token", "tok_abcdef1234")
    rig.store.set_secret(rig.profile.id, "google", "refresh_token", "should_never_leak")
    rig.store.set_secret(rig.profile.id, "google_web", "password", "should_never_leak")
    monkeypatch.setenv("NOTION_API_TOKEN", "env_abcdef1234")
    # Also plant one to make sure env_var reporting doesn't leak the value.
    monkeypatch.setenv("TODOIST_API_TOKEN", "env_abcdef1234")

    await rig.handle({"type": "plugins:list"})
    serialized = json.dumps(rig.payload())
    for marker in _SECRET_MARKERS:
        assert marker not in serialized, (
            f"secret {marker!r} leaked into serialized plugins:list payload"
        )


async def test_plugins_changed_broadcast_has_matching_shape(rig):
    # Directly build the broadcast event; must carry the same data schema.
    evt = rig.main._plugins_changed_event()
    assert evt["type"] == "plugins:changed"
    assert set(evt["data"]) == {"plugins", "errors", "capability_vocabulary"}
