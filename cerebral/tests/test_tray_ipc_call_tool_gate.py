"""Tray-IPC call_tool capability gate — Issue #238.

Closes the bypass identified in ADR-0005 Amendment 2: the WebSocket
``call_tool`` handler previously dispatched ``_orc.call_tool`` with no
capability argument, skipping the ACL + consent ladder entirely.

The fix mirrors the ``approve_item`` pattern but without ``passive=True``
because tray-IPC ``call_tool`` is a direct user action, not an ambient
queue candidate.

Three acceptance criteria exercised here:

1. A tool whose capability resolves SILENT dispatches and returns a
   ``tool_result`` broadcast with ``is_error=False``.
2. A tool whose capability is denied (DENY) returns a ``tool_result``
   broadcast with ``is_error=True`` — the plugin is never called.
3. A tool with an ask-class capability triggers the consent surface;
   if the user accepts, the tool dispatches successfully.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator, Tool, ToolResult
from cerebral.security import (
    Capability,
    CallFlags,
    Decision,
    ProfileACL,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RecordingConsent:
    """Duck-typed ConsentSurface that returns a queued decision per call."""

    def __init__(self, *decisions) -> None:
        self.decisions = list(decisions) or [Decision.SILENT]
        self.received: list[dict] = []

    async def request(self, capability, tool_name, args, flags=None):
        self.received.append({
            "capability": capability,
            "tool_name": tool_name,
            "flags": flags,
        })
        return self.decisions.pop(0) if self.decisions else Decision.SILENT

    def set_acl(self, acl) -> None:
        pass


def _make_read_plugin() -> MagicMock:
    """Fake plugin with a ``read_notes`` tool declaring ``fs_read``
    (SILENT by default — no consent needed for allowed calls)."""
    plugin = MagicMock()
    plugin.name = "notes"
    plugin.list_tools.return_value = [
        Tool(name="read_notes", description="Read notes", plugin="notes", schema={}),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="notes content"))
    return plugin


def _make_write_plugin() -> MagicMock:
    """Fake plugin with a ``write_file`` tool declaring ``fs_write``
    (ASK by default — needs consent unless a grant exists)."""
    plugin = MagicMock()
    plugin.name = "files"
    plugin.list_tools.return_value = [
        Tool(name="write_file", description="Write file", plugin="files", schema={}),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="written"))
    return plugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_main(main_mod, orc, tmp_path, *, active_profile=None):
    """Swap the relevant module-level singletons in cerebral.main for the
    duration of a test; return a (sent_list, saved_dict) tuple."""
    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = active_profile or pm.create(
        name="Tester", wake_name="felix", voice_id="af_heart",
    )
    pm.set_active(profile.id)

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    async def fake_record_turn(kind, content):
        pass

    saved = {k: getattr(main_mod, k) for k in (
        "_pm", "_orc", "_active_profile", "_connected",
        "_broadcast", "_record_turn",
    )}

    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._active_profile = profile
    main_mod._connected = set()
    main_mod._broadcast = fake_broadcast
    main_mod._record_turn = fake_record_turn

    return sent, saved


# ---------------------------------------------------------------------------
# AC 1 — allowed path
# ---------------------------------------------------------------------------


async def test_call_tool_allowed_dispatches_and_broadcasts(tmp_path):
    """SILENT-class capability → plugin dispatches; tool_result broadcast
    carries ``is_error=False``."""
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    plugin = _make_read_plugin()
    orc.register(plugin, required_capabilities=frozenset({"fs_read"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path)
    try:
        await main_mod._handle_message({
            "type": "call_tool",
            "data": {"name": "read_notes", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    plugin.call_tool.assert_called_once()
    result_events = [e for e in sent if e["type"] == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["is_error"] is False
    assert result_events[0]["data"]["name"] == "read_notes"


# ---------------------------------------------------------------------------
# AC 2 — denied path
# ---------------------------------------------------------------------------


async def test_call_tool_denied_returns_error_without_dispatch(tmp_path):
    """Denied capability → tool is NOT called; tool_result broadcast
    carries ``is_error=True`` with a denial message."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    # Explicitly deny fs_write for this profile.
    acl.set_persistent_class(Capability.FS_WRITE, Decision.DENY)

    # Consent surface that always refuses (should never be reached for DENY).
    consent = _RecordingConsent(Decision.DENY)

    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_write_plugin()
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    async def fake_record_turn(kind, content):
        pass

    saved = {k: getattr(main_mod, k) for k in (
        "_pm", "_orc", "_active_profile", "_connected",
        "_broadcast", "_record_turn",
    )}
    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._active_profile = profile
    main_mod._connected = set()
    main_mod._broadcast = fake_broadcast
    main_mod._record_turn = fake_record_turn

    try:
        await main_mod._handle_message({
            "type": "call_tool",
            "data": {"name": "write_file", "args": {"path": "/tmp/x"}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    plugin.call_tool.assert_not_called()
    result_events = [e for e in sent if e["type"] == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["is_error"] is True
    assert "deny" in result_events[0]["data"]["content"].lower()


# ---------------------------------------------------------------------------
# AC 3 — consent path
# ---------------------------------------------------------------------------


async def test_call_tool_ask_class_triggers_consent_and_dispatches_on_accept(tmp_path):
    """ASK-class capability with no prior grant: consent surface fires once;
    user accepts (SILENT) → tool dispatches, tool_result is_error=False."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    # No grant → fs_write defaults to ASK; consent surface will be called.
    consent = _RecordingConsent(Decision.SILENT)  # user accepts
    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_write_plugin()
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    async def fake_record_turn(kind, content):
        pass

    saved = {k: getattr(main_mod, k) for k in (
        "_pm", "_orc", "_active_profile", "_connected",
        "_broadcast", "_record_turn",
    )}
    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._active_profile = profile
    main_mod._connected = set()
    main_mod._broadcast = fake_broadcast
    main_mod._record_turn = fake_record_turn

    try:
        await main_mod._handle_message({
            "type": "call_tool",
            "data": {"name": "write_file", "args": {"path": "/tmp/y"}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    # Consent surface was called exactly once.
    assert len(consent.received) == 1, (
        "ask-class tray-IPC call must prompt the consent surface"
    )
    assert consent.received[0]["capability"] is Capability.FS_WRITE

    # After consent, the tool dispatched and the result is not an error.
    plugin.call_tool.assert_called_once()
    result_events = [e for e in sent if e["type"] == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["is_error"] is False


async def test_call_tool_ask_class_refused_at_consent_returns_error(tmp_path):
    """ASK-class capability, user denies at the consent surface: tool must
    NOT dispatch; tool_result carries is_error=True."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    consent = _RecordingConsent(Decision.DENY)  # user refuses
    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_write_plugin()
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    async def fake_record_turn(kind, content):
        pass

    saved = {k: getattr(main_mod, k) for k in (
        "_pm", "_orc", "_active_profile", "_connected",
        "_broadcast", "_record_turn",
    )}
    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._active_profile = profile
    main_mod._connected = set()
    main_mod._broadcast = fake_broadcast
    main_mod._record_turn = fake_record_turn

    try:
        await main_mod._handle_message({
            "type": "call_tool",
            "data": {"name": "write_file", "args": {"path": "/tmp/z"}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    plugin.call_tool.assert_not_called()
    result_events = [e for e in sent if e["type"] == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["is_error"] is True
    assert "deny" in result_events[0]["data"]["content"].lower()


# ---------------------------------------------------------------------------
# Per-tool capability scoping — the Skills-toggle bug regression
# ---------------------------------------------------------------------------
#
# A plugin declares the UNION of its tools' capabilities (skills =
# fs_read/fs_write/fs_delete/network). Before per-tool scoping, the tray gate
# checked that whole union for EVERY tool, so ``skill_enable`` (a settings
# write) inherited ``fs_delete`` and fired a "delete files" consent that
# blocked enabling a skill. Per-tool ``required_capabilities`` narrows the
# check to what each tool actually uses.


def _make_capscope_plugin() -> MagicMock:
    """Plugin declaring an fs_delete-inclusive union, with a write-only tool
    (per-tool fs_read+fs_write) and a delete tool (per-tool +fs_delete)."""
    plugin = MagicMock()
    plugin.name = "capscope"
    plugin.list_tools.return_value = [
        Tool(
            name="cap_write", description="write-only", plugin="capscope", schema={},
            required_capabilities=frozenset({"fs_read", "fs_write"}),
        ),
        Tool(
            name="cap_delete", description="deletes", plugin="capscope", schema={},
            required_capabilities=frozenset({"fs_read", "fs_write", "fs_delete"}),
        ),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    return plugin


async def test_per_tool_caps_write_tool_skips_plugin_fs_delete_consent(tmp_path):
    """A write-only tool is gated on its own {fs_read,fs_write} — NOT the
    plugin's fs_delete — so with fs_write granted it dispatches silently and
    never prompts the fs_delete consent (the Skills-toggle bug)."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)  # user allowed writes
    consent = _RecordingConsent(Decision.SILENT)

    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_capscope_plugin()
    # Plugin-wide union still includes fs_delete (like skills does for uninstall).
    orc.register(plugin, required_capabilities=frozenset(
        {"fs_read", "fs_write", "fs_delete"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path, active_profile=profile)
    try:
        await main_mod._handle_message({
            "type": "call_tool",
            "data": {"name": "cap_write", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    # No consent prompt at all — fs_delete was never consulted for this tool.
    assert consent.received == [], (
        "write-only tool must not inherit the plugin's fs_delete consent"
    )
    plugin.call_tool.assert_called_once()
    result_events = [e for e in sent if e["type"] == "tool_result"]
    assert len(result_events) == 1 and result_events[0]["data"]["is_error"] is False


async def test_per_tool_caps_delete_tool_still_prompts_fs_delete(tmp_path):
    """Scoping is real, not a blanket bypass: a tool that DOES declare
    fs_delete still routes fs_delete through the consent surface."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    consent = _RecordingConsent(Decision.SILENT)  # accept the fs_delete prompt

    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_capscope_plugin()
    orc.register(plugin, required_capabilities=frozenset(
        {"fs_read", "fs_write", "fs_delete"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path, active_profile=profile)
    try:
        await main_mod._handle_message({
            "type": "call_tool",
            "data": {"name": "cap_delete", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    assert len(consent.received) == 1
    assert consent.received[0]["capability"] is Capability.FS_DELETE
    plugin.call_tool.assert_called_once()
