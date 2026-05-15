"""Queue admission overlay — ambient-actuation defeat (Issue #52).

Headline regression for the #52 acceptance criteria: a persistent SILENT
grant on a capability MUST NOT silently dispatch a queue-originated call.
The queue path runs with ``passive=True`` which escalates ACL decisions
one notch post-resolution (SILENT → ASK, ASK → DENY) so a queued risky
action always reaches the consent surface, even when the wake-originated
equivalent would not.

These tests run the real orchestrator + real ProfileACL against a fake
consent surface — the same pattern used by #48 / #51 / #53.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.action_queue.manager import QueueManager
from cerebral.db.profiles import Profile, ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator, Tool, ToolResult
from cerebral.security import (
    Capability,
    CallFlags,
    Decision,
    ProfileACL,
)


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------


class _RecordingConsent:
    """Duck-typed ConsentSurface. Returns the queued decision, records calls."""

    def __init__(self, *decisions) -> None:
        self.decisions = list(decisions) or [Decision.SILENT]
        self.received: list[dict] = []

    async def request(self, capability, tool_name, args, flags=None):
        self.received.append({
            "capability": capability,
            "tool_name": tool_name,
            "args": dict(args or {}),
            "flags": flags,
        })
        return self.decisions.pop(0) if self.decisions else Decision.SILENT

    def set_acl(self, acl) -> None:
        pass


def _make_phone_plugin() -> MagicMock:
    """Fake phone plugin with a ``send_message`` tool, REQUIRED_CAPABILITIES =
    {external_data_write}. ``call_tool`` is an AsyncMock so the test can
    assert whether the tool was dispatched."""
    plugin = MagicMock()
    plugin.name = "phone"
    plugin.list_tools.return_value = [
        Tool(
            name="send_message",
            description="Send a text message",
            plugin="phone",
            schema={},
        ),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="sent"))
    return plugin


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
def queue(tmp_path) -> QueueManager:
    return QueueManager(str(tmp_path / "queue.db"))


# ---------------------------------------------------------------------------
# Headline AC — persistent SILENT grant + queue-originated call
# ---------------------------------------------------------------------------


async def test_persistent_silent_grant_does_not_bypass_queue_consent(acl):
    """The headline AC: a persistent SILENT grant on external_data_write
    must NOT auto-dispatch a queue-originated send. The ACL escalates
    SILENT → ASK under passive=True, and the orchestrator routes ASK to
    the consent surface — so the user gets prompted even though they
    "already allowed" the class."""
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(acl=acl, consent=consent)
    orc.register(_make_phone_plugin(), required_capabilities=frozenset({"external_data_write"}))

    # Persistent SILENT grant — the user said "always allow" for this class.
    acl.set_persistent_class(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)

    decision = await orc.check_capabilities(
        "send_message",
        frozenset({"external_data_write"}),
        CallFlags(passive=True),
    )

    # The passive escalation defeated the persistent SILENT, routed
    # through consent, the user accepted → final decision SILENT.
    assert decision is Decision.SILENT
    assert len(consent.received) == 1, (
        "consent surface MUST have prompted; the persistent SILENT was "
        "supposed to be defeated by passive=True"
    )
    assert consent.received[0]["capability"] is Capability.EXTERNAL_DATA_WRITE


async def test_session_silent_grant_does_not_bypass_queue_consent(acl):
    """Same regression for session-scoped grants."""
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(acl=acl, consent=consent)
    orc.register(_make_phone_plugin(), required_capabilities=frozenset({"external_data_write"}))

    # Session SILENT grant — the user clicked "session" in a prior prompt.
    acl.grant_session(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)

    decision = await orc.check_capabilities(
        "send_message",
        frozenset({"external_data_write"}),
        CallFlags(passive=True),
    )

    assert decision is Decision.SILENT
    assert len(consent.received) == 1


async def test_wake_originated_send_dispatches_silently_under_silent_grant(acl):
    """Counterpart: wake-originated equivalent (no passive flag) DOES
    dispatch silently. This is what differentiates queue from wake —
    the visibility of the user's command implies consent for the wake
    path; the silent ambient extraction does not.
    """
    consent = _RecordingConsent()  # never called
    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_phone_plugin()
    orc.register(plugin, required_capabilities=frozenset({"external_data_write"}))

    acl.set_persistent_class(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)

    # Wake path: no passive flag.
    result = await orc.call_tool(
        "send_message",
        {"to": "John", "text": "on my way"},
        capability=Capability.EXTERNAL_DATA_WRITE,
        flags=CallFlags(),  # passive defaults False
    )

    assert not result.is_error
    plugin.call_tool.assert_called_once()
    assert consent.received == [], (
        "wake-originated send under a persistent SILENT grant must dispatch "
        "silently; the consent surface should not have been touched"
    )


async def test_queue_path_denies_when_consent_refused(acl):
    """When the user refuses at the consent prompt, the queue-originated
    tool must NOT dispatch."""
    consent = _RecordingConsent(Decision.DENY)
    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_phone_plugin()
    orc.register(plugin, required_capabilities=frozenset({"external_data_write"}))

    acl.set_persistent_class(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)

    decision = await orc.check_capabilities(
        "send_message",
        frozenset({"external_data_write"}),
        CallFlags(passive=True),
    )

    assert decision is Decision.DENY
    assert len(consent.received) == 1
    plugin.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Risky badge propagates through the queue → IPC payload contract
# ---------------------------------------------------------------------------


def test_risky_send_is_queueable_and_flagged(queue):
    """The risk heuristic is a visibility cue, not a gate — the item
    queues normally, and the IPC payload carries ``risky: True``."""
    sendy = queue.add_item(
        "Send John a message",
        "Felix overheard the user say they'd text John",
        tool_name="send_message",
        tool_args={"to": "John", "text": "on my way"},
    )
    notes = queue.add_item("Read my notes", "Look up the meeting notes")

    pending = queue.get_pending()
    by_title = {i.title: i for i in pending}
    assert by_title["Send John a message"].risky is True
    assert by_title["Read my notes"].risky is False

    # IPC payload — what the tray actually receives via queue_update.
    payload = [i.to_dict() for i in pending]
    risk_by_title = {p["title"]: p["risky"] for p in payload}
    assert risk_by_title["Send John a message"] is True
    assert risk_by_title["Read my notes"] is False

    # The risky item is approvable just like any other — the badge does
    # not block admission.
    approved = queue.approve_item(sendy.id)
    assert approved is not None
    assert approved.status == "approved"


# ---------------------------------------------------------------------------
# AND-semantics smoke: a plugin declaring two caps gets the worst one
# resolved against the ACL.
# ---------------------------------------------------------------------------


async def test_multiple_caps_take_worst_under_passive(acl):
    """A plugin declaring {external_data_write, fs_write} with persistent
    SILENT grants on both: passive escalation lifts each from SILENT to
    ASK; worst-across-set is ASK; consent routes once. AND semantics
    preserved without nagging the user per cap.
    """
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(acl=acl, consent=consent)

    plugin = MagicMock()
    plugin.name = "messy"
    plugin.list_tools.return_value = [
        Tool(name="multi_tool", description="d", plugin="messy", schema={}),
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    orc.register(
        plugin,
        required_capabilities=frozenset({"external_data_write", "fs_write"}),
    )

    # Both classes are ASK by default; without grants, passive escalates
    # each to DENY and consent never fires (the defeat is total). To show
    # the AND-then-single-prompt behaviour we grant both classes a
    # persistent SILENT — the realistic "user has allowed both for non-
    # queue contexts" scenario.
    acl.set_persistent_class(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)
    acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)

    decision = await orc.check_capabilities(
        "multi_tool",
        frozenset({"external_data_write", "fs_write"}),
        CallFlags(passive=True),
    )

    assert decision is Decision.SILENT  # consent accepted after one prompt
    assert len(consent.received) == 1, "consent must prompt once, not per cap"


async def test_queue_path_with_no_grant_denies_without_prompting(acl):
    """Symmetric to the previous test: when the user has NOT granted the
    class, passive on an ASK-default class escalates to DENY at ACL
    resolution time — consent doesn't even fire. The queue-originated
    call is silently refused (the user can re-issue via wake to consent
    in-the-moment). This is ADR-0005's "ambient-actuation defeat" at
    full strength.
    """
    consent = _RecordingConsent()  # never called
    orc = MCPOrchestrator(acl=acl, consent=consent)
    plugin = _make_phone_plugin()
    orc.register(plugin, required_capabilities=frozenset({"external_data_write"}))

    # No grant — external_data_write defaults to ASK.

    decision = await orc.check_capabilities(
        "send_message",
        frozenset({"external_data_write"}),
        CallFlags(passive=True),
    )

    assert decision is Decision.DENY
    assert consent.received == [], (
        "with no grant, passive escalation defeats ASK to DENY without "
        "bothering the user"
    )
    plugin.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# main.py approve_item handler — end-to-end IPC wiring (Issue #52)
# ---------------------------------------------------------------------------


async def test_approve_item_handler_dispatches_tool_under_silent_grant(tmp_path):
    """End-to-end through the real ``_handle_message`` IPC handler:
    persistent SILENT grant + auto-allow consent → tool dispatches once,
    queue_item_result broadcast carries ``is_error=False``.
    """
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    orc = MCPOrchestrator()
    acl_local = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl_local.set_persistent_class(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)
    orc.set_acl(acl_local)
    consent = _RecordingConsent(Decision.SILENT)
    orc.set_consent_surface(consent)

    plugin = _make_phone_plugin()
    orc.register(plugin, required_capabilities=frozenset({"external_data_write"}))

    queue = QueueManager(str(tmp_path / "q.db"))
    item = queue.add_item(
        "Send John a message",
        "summary",
        tool_name="send_message",
        tool_args={"to": "John"},
    )

    saved = {
        "_pm": main_mod._pm,
        "_orc": main_mod._orc,
        "_queue": main_mod._queue,
        "_active_profile": main_mod._active_profile,
        "_connected": main_mod._connected,
        "_broadcast": main_mod._broadcast,
    }
    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._queue = queue
    main_mod._active_profile = profile
    main_mod._connected = set()
    main_mod._broadcast = fake_broadcast

    try:
        await main_mod._handle_message({
            "type": "approve_item",
            "data": {"item_id": item.id},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    # The persistent SILENT was defeated by passive escalation → consent
    # prompted once → user accepted → tool dispatched → success broadcast.
    assert len(consent.received) == 1
    plugin.call_tool.assert_called_once_with("send_message", {"to": "John"})

    result_events = [e for e in sent if e["type"] == "queue_item_result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["is_error"] is False
    assert result_events[0]["data"]["item_id"] == item.id


async def test_approve_item_handler_refuses_when_consent_denied(tmp_path):
    """End-to-end refusal: persistent SILENT + DENY at consent → tool
    NEVER dispatches, queue_item_result carries the structured refusal."""
    import cerebral.main as main_mod

    pm = ProfileManager(db_path=tmp_path / "perm.db")
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    orc = MCPOrchestrator()
    acl_local = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl_local.set_persistent_class(Capability.EXTERNAL_DATA_WRITE, Decision.SILENT)
    orc.set_acl(acl_local)
    consent = _RecordingConsent(Decision.DENY)
    orc.set_consent_surface(consent)

    plugin = _make_phone_plugin()
    orc.register(plugin, required_capabilities=frozenset({"external_data_write"}))

    queue = QueueManager(str(tmp_path / "q.db"))
    item = queue.add_item(
        "Send John a message",
        "summary",
        tool_name="send_message",
        tool_args={"to": "John"},
    )

    saved = {
        "_pm": main_mod._pm,
        "_orc": main_mod._orc,
        "_queue": main_mod._queue,
        "_active_profile": main_mod._active_profile,
        "_connected": main_mod._connected,
        "_broadcast": main_mod._broadcast,
    }
    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._queue = queue
    main_mod._active_profile = profile
    main_mod._connected = set()
    main_mod._broadcast = fake_broadcast

    try:
        await main_mod._handle_message({
            "type": "approve_item",
            "data": {"item_id": item.id},
        })
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    plugin.call_tool.assert_not_called()
    result_events = [e for e in sent if e["type"] == "queue_item_result"]
    assert len(result_events) == 1
    assert result_events[0]["data"]["is_error"] is True
    assert "deny" in result_events[0]["data"]["result"].lower()
