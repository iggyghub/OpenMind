"""
Unit tests for cerebral/channel_inbox.py + the S18 wiring in main.py.

Issue #301. The store itself is in-RAM and side-effect-free; main.py's
helpers (``_channel_inbox_observer`` / ``_send_channel_reply`` / the
``send_channel_reply`` and ``request_channel_inbox`` dispatcher branches)
are exercised against patched stubs so no orchestrator / no openclaw
subprocess is spawned.
"""
from __future__ import annotations

import json

import pytest

from cerebral.channel_inbox import ChannelInbox


# ── ChannelInbox store ───────────────────────────────────────────────────────


def test_default_snapshot_empty():
    inbox = ChannelInbox()
    assert inbox.snapshot() == []


def test_record_inbound_and_outbound_preserve_order_and_direction():
    inbox = ChannelInbox()
    inbox.record_inbound("telegram:u1", "hello", auto_reply="hi back", ts=10.0)
    inbox.record_outbound("telegram:u1", "manual reply", ts=11.0)
    snap = inbox.snapshot()
    assert len(snap) == 2
    assert [e["direction"] for e in snap] == ["inbound", "outbound"]
    assert snap[0]["text"] == "hello"
    assert snap[0]["auto_reply"] == "hi back"
    assert snap[0]["ts"] == 10.0
    assert snap[1]["text"] == "manual reply"
    assert snap[1]["auto_reply"] is None
    # Monotonic id field for UI keying.
    assert snap[1]["id"] == snap[0]["id"] + 1


def test_record_inbound_accepts_none_auto_reply():
    inbox = ChannelInbox()
    inbox.record_inbound("discord:c1", "hi")
    snap = inbox.snapshot()
    assert snap[0]["auto_reply"] is None
    assert snap[0]["text"] == "hi"


def test_bounded_max_entries_drops_oldest():
    inbox = ChannelInbox(max_entries=10)
    for i in range(25):
        inbox.record_inbound("telegram:u1", f"m{i}", ts=float(i))
    snap = inbox.snapshot()
    assert len(snap) == 10
    # Oldest dropped; only m15..m24 remain.
    texts = [e["text"] for e in snap]
    assert texts == [f"m{i}" for i in range(15, 25)]


def test_clear_drops_all_entries():
    inbox = ChannelInbox()
    inbox.record_inbound("a:1", "hi")
    inbox.record_outbound("a:1", "yo")
    inbox.clear()
    assert inbox.snapshot() == []


# ── main.py dispatcher integration ───────────────────────────────────────────
#
# Pattern lifted from test_harness_channels.py's harness_rig: patch the
# few module-level seams the dispatcher reaches into (the inbox store, the
# broadcast hook, the orchestrator call_tool seam), exercise
# _handle_message directly, assert on captured broadcasts.


@pytest.fixture
def inbox_rig():
    import cerebral.main as main_mod

    sent: list[dict] = []
    tool_calls: list[tuple[str, dict]] = []
    tool_result = {"is_error": False, "content": ""}

    class _FakeToolResult:
        def __init__(self, is_error=False, content=""):
            self.is_error = is_error
            self.content = content

    class _FakeOrc:
        async def call_tool(self, name, args):
            tool_calls.append((name, dict(args)))
            return _FakeToolResult(
                is_error=tool_result["is_error"],
                content=tool_result["content"],
            )

    fresh_inbox = ChannelInbox()

    async def fake_broadcast(event):
        sent.append(event)

    saved = {
        "_channel_inbox": main_mod._channel_inbox,
        "_orc":           main_mod._orc,
        "_broadcast":     main_mod._broadcast,
        "_connected":     main_mod._connected,
    }
    main_mod._channel_inbox = fresh_inbox
    main_mod._orc           = _FakeOrc()
    main_mod._broadcast     = fake_broadcast
    main_mod._connected     = set()

    class Rig:
        def __init__(self):
            self.store       = fresh_inbox
            self.sent        = sent
            self.tool_calls  = tool_calls
            self.tool_result = tool_result

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def inbox_events(self):
            return [e for e in self.sent if e["type"] == "channel_inbox_update"]

        def last_inbox(self):
            return self.inbox_events()[-1]["data"]

    try:
        yield Rig()  # noqa: F841
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)


async def test_request_channel_inbox_broadcasts_current_snapshot(inbox_rig):
    inbox_rig.store.record_inbound("telegram:u1", "ping", auto_reply="pong")
    await inbox_rig.handle({"type": "request_channel_inbox"})
    assert len(inbox_rig.inbox_events()) == 1
    entries = inbox_rig.last_inbox()["entries"]
    assert entries[0]["text"] == "ping"
    assert entries[0]["auto_reply"] == "pong"
    assert entries[0]["direction"] == "inbound"


async def test_send_channel_reply_routes_through_messages_send(inbox_rig):
    await inbox_rig.handle({
        "type": "send_channel_reply",
        "data": {"session_key": "telegram:u1", "text": "manual hi"},
    })
    # Orchestrator was called with the openclaw_ prefixed tool name.
    assert inbox_rig.tool_calls == [
        ("openclaw_messages_send",
         {"session_key": "telegram:u1", "text": "manual hi"}),
    ]
    # Outbound entry recorded, broadcast emitted.
    entries = inbox_rig.last_inbox()["entries"]
    assert entries[-1]["direction"] == "outbound"
    assert entries[-1]["text"] == "manual hi"


async def test_send_channel_reply_does_not_record_on_orchestrator_error(inbox_rig):
    inbox_rig.tool_result["is_error"] = True
    inbox_rig.tool_result["content"] = "Denied"
    await inbox_rig.handle({
        "type": "send_channel_reply",
        "data": {"session_key": "telegram:u1", "text": "manual hi"},
    })
    assert inbox_rig.tool_calls  # the call was attempted
    # No outbound entry, no broadcast.
    assert inbox_rig.store.snapshot() == []
    assert inbox_rig.inbox_events() == []


async def test_send_channel_reply_rejects_missing_fields(inbox_rig):
    await inbox_rig.handle({
        "type": "send_channel_reply",
        "data": {"session_key": "", "text": "hi"},
    })
    await inbox_rig.handle({
        "type": "send_channel_reply",
        "data": {"session_key": "telegram:u1", "text": "   "},
    })
    assert inbox_rig.tool_calls == []
    assert inbox_rig.inbox_events() == []


async def test_channel_inbox_observer_records_and_broadcasts(inbox_rig):
    import cerebral.main as main_mod
    await main_mod._channel_inbox_observer(
        "discord:c1", "hi from discord", "hello back",
    )
    snap = inbox_rig.store.snapshot()
    assert len(snap) == 1
    assert snap[0]["session_key"] == "discord:c1"
    assert snap[0]["text"] == "hi from discord"
    assert snap[0]["auto_reply"] == "hello back"
    assert inbox_rig.last_inbox()["entries"][0]["text"] == "hi from discord"


# ── greeting includes the inbox snapshot ─────────────────────────────────────


async def test_greet_includes_channel_inbox_event(inbox_rig):
    import cerebral.main as main_mod

    inbox_rig.store.record_inbound("telegram:u1", "ping", auto_reply="pong")

    class _FakeWebsocket:
        def __init__(self):
            self.sent_raw = []

        async def send(self, payload):
            self.sent_raw.append(payload)

    ws = _FakeWebsocket()
    # _greet is the bound method on the module
    await main_mod._greet(ws)
    sent_events = [json.loads(p) for p in ws.sent_raw]
    types = [e.get("type") for e in sent_events]
    assert "channel_inbox_update" in types
    # And it carries the current snapshot.
    inbox_evt = [e for e in sent_events if e["type"] == "channel_inbox_update"][0]
    assert inbox_evt["data"]["entries"][0]["text"] == "ping"
