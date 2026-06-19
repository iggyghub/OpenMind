"""
Settings-control plugin tests -- F4 (#327).

Exercises the voice/typed settings-control surface:
  * plugins/settings_control.py registers a ``set_system_setting`` tool
    declaring ``REQUIRED_CAPABILITIES = {"fs_write"}``.
  * Routed through ``MCPOrchestrator.call_tool`` with
    ``capability=Capability.FS_WRITE`` it triggers the ConsentSurface
    (FS_WRITE is ASK-class). The plugin's apply callback is invoked only
    when the user accepts; a deny short-circuits before the apply runs.
  * Unknown keys and a missing apply callback fail with a clear
    ToolResult (is_error=True) instead of silently no-op'ing.

Pattern modelled on cerebral/tests/test_consent_surface.py.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator
from cerebral.security import (
    CHOICE_DENY,
    CHOICE_PERSISTENT,
    Capability,
    CallFlags,
    ConsentRequest,
    ConsentSurface,
    Decision,
    ProfileACL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm(tmp_path) -> ProfileManager:
    return ProfileManager(db_path=tmp_path / "openmind.db")


@pytest.fixture
def profile(pm: ProfileManager) -> Profile:
    return pm.create(name="Alice", wake_name="felix", voice_id="af_heart")


@pytest.fixture
def acl(pm: ProfileManager, profile: Profile) -> ProfileACL:
    return ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )


@pytest.fixture
def settings_control():
    """Fresh import of the plugin module per-test.

    set_apply_callback writes module-level state; reloading isolates
    tests from each other regardless of ordering.
    """
    import plugins.settings_control as mod
    importlib.reload(mod)
    yield mod
    mod.set_apply_callback(None)


class _FakePrompt:
    def __init__(self, *choices) -> None:
        self.choices = list(choices)
        self.received: list[ConsentRequest] = []

    async def __call__(self, req: ConsentRequest) -> str:
        self.received.append(req)
        if not self.choices:
            raise AssertionError("no scripted choice for this prompt")
        return self.choices.pop(0)


def _build_orc(plugin_mod, acl, prompt_fn) -> MCPOrchestrator:
    surface = ConsentSurface(
        prompt_fn=prompt_fn,
        has_subscriber_fn=lambda: True,
        acl=acl,
        request_id_fn=lambda: "req-1",
    )
    orc = MCPOrchestrator(acl=acl, consent=surface)
    orc.register(
        plugin_mod.create(),
        required_capabilities=plugin_mod.REQUIRED_CAPABILITIES,
    )
    return orc


# ---------------------------------------------------------------------------
# Plugin shape
# ---------------------------------------------------------------------------


def test_plugin_declares_fs_write_capability(settings_control):
    """ADR-0005 ask-class gating depends on FS_WRITE being declared."""
    assert settings_control.REQUIRED_CAPABILITIES == frozenset({"fs_write"})


def test_plugin_exposes_one_tool_with_enum_key(settings_control):
    plugin = settings_control.create()
    tools = plugin.list_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "set_system_setting"
    assert tool.plugin == settings_control.PLUGIN_NAME
    assert tool.irreversible is False
    enum = tool.schema["properties"]["key"]["enum"]
    # Bounded set -- system + appearance keys, no profile-scoped state.
    assert set(enum) == settings_control.ALLOWED_KEYS
    # Profile-scoped keys must NOT be reachable through this tool.
    for forbidden in ("voice", "wake_name", "voice_id", "memory"):
        assert forbidden not in enum


# ---------------------------------------------------------------------------
# call_tool basic shape (no gate)
# ---------------------------------------------------------------------------


async def test_call_tool_unknown_key_errors(settings_control):
    plugin = settings_control.create()
    result = await plugin.call_tool(
        "set_system_setting", {"key": "wake_name", "value": "lucy"},
    )
    assert result.is_error
    assert "Unsupported setting key" in result.content


async def test_call_tool_missing_value_errors(settings_control):
    plugin = settings_control.create()
    result = await plugin.call_tool(
        "set_system_setting", {"key": "tts_volume"},
    )
    assert result.is_error
    assert "Missing 'value'" in result.content


async def test_call_tool_without_wired_callback_errors(settings_control):
    plugin = settings_control.create()
    settings_control.set_apply_callback(None)
    result = await plugin.call_tool(
        "set_system_setting", {"key": "tts_volume", "value": 50},
    )
    assert result.is_error
    assert "not wired" in result.content


async def test_call_tool_invokes_wired_callback(settings_control):
    plugin = settings_control.create()
    calls: list[tuple[str, object]] = []

    async def apply(key, value):
        calls.append((key, value))

    settings_control.set_apply_callback(apply)
    result = await plugin.call_tool(
        "set_system_setting", {"key": "tts_volume", "value": 50},
    )
    assert not result.is_error
    assert calls == [("tts_volume", 50)]


async def test_callback_value_error_surfaces_in_tool_result(settings_control):
    plugin = settings_control.create()

    async def apply(key, value):
        raise ValueError("volume must be 0-100")

    settings_control.set_apply_callback(apply)
    result = await plugin.call_tool(
        "set_system_setting", {"key": "tts_volume", "value": 9999},
    )
    assert result.is_error
    assert "volume must be 0-100" in result.content
    assert "tts_volume" in result.content


async def test_unknown_tool_name_errors(settings_control):
    plugin = settings_control.create()
    result = await plugin.call_tool("clearly_not_a_tool", {})
    assert result.is_error


async def test_appearance_keys_routed_through_callback(settings_control):
    """ui_theme / ui_scale / ui_accent reach the apply callback too."""
    plugin = settings_control.create()
    calls: list[tuple[str, object]] = []

    async def apply(key, value):
        calls.append((key, value))

    settings_control.set_apply_callback(apply)
    for key, value in [
        ("ui_theme", "light"),
        ("ui_scale", "1.25"),
        ("ui_accent", "#ff0066"),
    ]:
        result = await plugin.call_tool(
            "set_system_setting", {"key": key, "value": value},
        )
        assert not result.is_error
    assert calls == [
        ("ui_theme", "light"),
        ("ui_scale", "1.25"),
        ("ui_accent", "#ff0066"),
    ]


# ---------------------------------------------------------------------------
# ADR-0005 gate integration through the orchestrator
# ---------------------------------------------------------------------------


async def test_consent_accept_invokes_apply_and_returns_ok(
    settings_control, acl,
):
    """passive=False on FS_WRITE -> ASK -> consent surface -> accept -> apply."""
    calls: list[tuple[str, object]] = []

    async def apply(key, value):
        calls.append((key, value))

    settings_control.set_apply_callback(apply)
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    orc = _build_orc(settings_control, acl, prompt)

    result = await orc.call_tool(
        "set_system_setting",
        {"key": "tts_volume", "value": 50},
        capability=Capability.FS_WRITE,
        flags=CallFlags(passive=False),
    )
    assert not result.is_error
    assert calls == [("tts_volume", 50)]
    assert len(prompt.received) == 1
    assert prompt.received[0].capability is Capability.FS_WRITE
    assert prompt.received[0].tool_name == "set_system_setting"


async def test_consent_deny_blocks_apply(settings_control, acl):
    """A DENY short-circuits BEFORE the plugin's call_tool runs."""
    calls: list[tuple[str, object]] = []

    async def apply(key, value):
        calls.append((key, value))

    settings_control.set_apply_callback(apply)
    prompt = _FakePrompt(CHOICE_DENY)
    orc = _build_orc(settings_control, acl, prompt)

    result = await orc.call_tool(
        "set_system_setting",
        {"key": "tts_volume", "value": 50},
        capability=Capability.FS_WRITE,
        flags=CallFlags(passive=False),
    )
    assert result.is_error
    assert calls == []  # AC: no apply without approval


async def test_no_consent_surface_fails_closed(settings_control, acl):
    """Without a wired surface the orchestrator denies ASK calls (pre-#48)."""
    calls: list[tuple[str, object]] = []

    async def apply(key, value):
        calls.append((key, value))

    settings_control.set_apply_callback(apply)
    plugin = settings_control.create()
    orc = MCPOrchestrator(acl=acl)  # no consent surface
    orc.register(
        plugin, required_capabilities=settings_control.REQUIRED_CAPABILITIES,
    )

    result = await orc.call_tool(
        "set_system_setting",
        {"key": "tts_volume", "value": 50},
        capability=Capability.FS_WRITE,
        flags=CallFlags(passive=False),
    )
    assert result.is_error
    assert calls == []


async def test_check_capabilities_routes_to_consent(settings_control, acl):
    """ChainEngine -> _orc.check_capabilities(..., CallFlags()) path (main.py).

    passive=False on FS_WRITE -> ASK -> consent surface fires once.
    """
    settings_control.set_apply_callback(lambda *_a, **_k: None)  # not exercised
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    orc = _build_orc(settings_control, acl, prompt)

    decision = await orc.check_capabilities(
        "set_system_setting", frozenset({"fs_write"}), CallFlags(),
    )
    assert decision is Decision.SILENT  # user accepted
    assert len(prompt.received) == 1
