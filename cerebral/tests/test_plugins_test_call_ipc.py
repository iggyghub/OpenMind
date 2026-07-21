"""plugins:test_call IPC tests -- Harness UI rework, S4 #472.

Covers spec section 5.3:
  - request {tool_name, args, thread} -> response {is_error, content_preview}
  - is_error passthrough from ToolResult
  - content_preview truncated to 500 chars
  - plugin exception surfaces as is_error=True (orchestrator never-raise)
  - permissions gate applied (denied capability blocks dispatch)
  - _record_turn recording still fires (shared path with call_tool)
  - no secret pattern in the serialized response payload (SAFETY #2)

Uses the shared tray-IPC path in cerebral.main (_dispatch_tray_call_tool),
so a passing test here also protects the existing call_tool contract.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator, Tool, ToolResult
from cerebral.security import (
    Capability,
    Decision,
    ProfileACL,
)


class _RecordingConsent:
    def __init__(self, *decisions) -> None:
        self.decisions = list(decisions) or [Decision.SILENT]
        self.received: list[dict] = []

    async def request(self, capability, tool_name, args, flags=None):
        self.received.append({"capability": capability, "tool_name": tool_name})
        return self.decisions.pop(0) if self.decisions else Decision.SILENT

    def set_acl(self, acl) -> None:
        pass


def _make_echo_plugin(payload: str = "ok") -> MagicMock:
    plugin = MagicMock()
    plugin.name = "notes"
    plugin.list_tools.return_value = [
        Tool(name="read_notes", description="Read notes", plugin="notes", schema={}),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content=payload))
    return plugin


def _make_raising_plugin() -> MagicMock:
    plugin = MagicMock()
    plugin.name = "notes"
    plugin.list_tools.return_value = [
        Tool(name="read_notes", description="Read notes", plugin="notes", schema={}),
    ]
    plugin.call_tool = AsyncMock(side_effect=RuntimeError("kaboom"))
    return plugin


def _patch_main(main_mod, orc, tmp_path, *, records=None):
    """Swap module singletons; return (sent_list, saved_dict). If ``records``
    is a list, KIND_TOOL_CALL/KIND_TOOL_RESULT entries land in it."""
    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    async def fake_record_turn(kind, content, **_kw):
        if records is not None:
            records.append((kind, content))

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


async def test_test_call_allowed_returns_preview(tmp_path):
    """Happy path: silent-class tool -> {is_error: False, content_preview: str}."""
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    orc.register(_make_echo_plugin("hello"), required_capabilities=frozenset({"fs_read"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path)
    try:
        await main_mod._handle_message({
            "type": "plugins:test_call",
            "data": {"tool_name": "read_notes", "args": {}, "thread": "harness-test"},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    events = [e for e in sent if e["type"] == "plugins:test_call"]
    assert len(events) == 1
    data = events[0]["data"]
    assert data["tool_name"] == "read_notes"
    assert data["is_error"] is False
    assert data["content_preview"] == "hello"


async def test_test_call_preview_truncated_to_500(tmp_path):
    """Content longer than 500 chars is truncated in the preview."""
    import cerebral.main as main_mod

    huge = "x" * 5000
    orc = MCPOrchestrator()
    orc.register(_make_echo_plugin(huge), required_capabilities=frozenset({"fs_read"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path)
    try:
        await main_mod._handle_message({
            "type": "plugins:test_call",
            "data": {"tool_name": "read_notes", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    events = [e for e in sent if e["type"] == "plugins:test_call"]
    assert events[0]["data"]["content_preview"] == "x" * 500


async def test_test_call_plugin_exception_surfaces_as_is_error(tmp_path):
    """Plugin raises -> is_error: True (never-raise contract holds)."""
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    orc.register(_make_raising_plugin(), required_capabilities=frozenset({"fs_read"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path)
    try:
        await main_mod._handle_message({
            "type": "plugins:test_call",
            "data": {"tool_name": "read_notes", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    events = [e for e in sent if e["type"] == "plugins:test_call"]
    assert len(events) == 1
    assert events[0]["data"]["is_error"] is True
    assert "kaboom" in events[0]["data"]["content_preview"]


async def test_test_call_denied_capability_blocks_dispatch(tmp_path):
    """Denied capability -> plugin.call_tool NOT invoked; response is_error=True."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)
    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl.set_persistent_class(Capability.FS_WRITE, Decision.DENY)

    orc = MCPOrchestrator(acl=acl, consent=_RecordingConsent(Decision.DENY))
    plugin = MagicMock()
    plugin.name = "files"
    plugin.list_tools.return_value = [
        Tool(name="write_file", description="Write", plugin="files", schema={}),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="written"))
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    async def fake_record_turn(kind, content, **_kw):
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
            "type": "plugins:test_call",
            "data": {"tool_name": "write_file", "args": {"path": "/tmp/x"}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    plugin.call_tool.assert_not_called()
    events = [e for e in sent if e["type"] == "plugins:test_call"]
    assert len(events) == 1
    assert events[0]["data"]["is_error"] is True
    assert "deny" in events[0]["data"]["content_preview"].lower()


async def test_test_call_records_turn_pair(tmp_path):
    """The shared path fires KIND_TOOL_CALL + KIND_TOOL_RESULT into _record_turn
    exactly as the direct call_tool path does -- the transcript reflects a
    harness test-fire, not just a silent dispatch."""
    from cerebral.db.conversation import KIND_TOOL_CALL, KIND_TOOL_RESULT
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    orc.register(_make_echo_plugin("ok"), required_capabilities=frozenset({"fs_read"}))

    records: list[tuple[str, dict]] = []
    sent, saved = _patch_main(main_mod, orc, tmp_path, records=records)
    try:
        await main_mod._handle_message({
            "type": "plugins:test_call",
            "data": {"tool_name": "read_notes", "args": {"k": "v"}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    kinds = [r[0] for r in records]
    assert KIND_TOOL_CALL in kinds
    assert KIND_TOOL_RESULT in kinds


async def test_test_call_missing_tool_name_is_noop(tmp_path):
    """Empty tool_name -> no dispatch, no response event."""
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    plugin = _make_echo_plugin("ok")
    orc.register(plugin, required_capabilities=frozenset({"fs_read"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path)
    try:
        await main_mod._handle_message({
            "type": "plugins:test_call",
            "data": {"tool_name": "", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    plugin.call_tool.assert_not_called()
    events = [e for e in sent if e["type"] == "plugins:test_call"]
    assert events == []


async def test_test_call_no_secret_pattern_in_payload(tmp_path):
    """SAFETY #2: even when the tool returns something that LOOKS like a
    secret, the transport carries it as content -- but the test-call
    response envelope itself must not carry raw credential fields keyed as
    ``password``/``token``/``secret``/``key``. Grep-based check mirrors
    test_plugins_list_ipc's SAFETY test."""
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    orc.register(_make_echo_plugin("harmless"), required_capabilities=frozenset({"fs_read"}))

    sent, saved = _patch_main(main_mod, orc, tmp_path)
    try:
        await main_mod._handle_message({
            "type": "plugins:test_call",
            "data": {"tool_name": "read_notes", "args": {}},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    raw = json.dumps(sent)
    assert not re.search(r'"(password|secret|token|key)":\s*"[^"]{8,}"', raw)
