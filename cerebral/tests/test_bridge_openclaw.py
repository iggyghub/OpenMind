"""
Tests for cerebral/bridge/openclaw.py — Issue #22.

OpenClaw channel bridge: a client that connects Cerebral to OpenClaw's inbound
message stream and posts replies back. OpenClaw is the external Node.js
gateway at http://localhost:3000 — it handles all channel I/O (Telegram,
WhatsApp, Discord, ...). The bridge is the seam where ambient channel
messages enter Cerebral's command pipeline.

Tests inject `process_fn`, `fetch_fn`, and `ws_connect_fn` to keep the unit
suite hermetic. No live OpenClaw or network is required.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from cerebral.bridge.openclaw import ChannelBridge


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class FakeWS:
    """Minimal async-iterable websocket double.

    `messages` is a list of strings (JSON-encoded inbound payloads) the fake
    will deliver one by one. After the list is exhausted the iterator stops.
    `closed` flips True when `.close()` is awaited.
    """

    def __init__(self, messages: list[str] | None = None) -> None:
        self.messages = list(messages or [])
        self.sent: list[str] = []
        self.closed = False
        self._ev = asyncio.Event()  # set by stop() to break the iterator

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return self.messages.pop(0)
        # block until close() so callers can drive the loop deterministically
        await self._ev.wait()
        raise StopAsyncIteration

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True
        self._ev.set()


# ---------------------------------------------------------------------------
# Slice 1 — handle_inbound: route inbound message → process_fn → outbound POST
# ---------------------------------------------------------------------------

async def test_inbound_message_calls_process_fn_with_text():
    process_fn = AsyncMock(return_value="It is 3:30 PM.")
    fetch_fn = AsyncMock(return_value={"ok": True})
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=fetch_fn)

    await bridge.handle_inbound({
        "channel": "telegram",
        "sender_id": "u1",
        "text": "Felix, what time is it?",
    })

    process_fn.assert_called_once()
    transcript = process_fn.call_args.args[0]
    assert transcript == "Felix, what time is it?"


async def test_inbound_message_posts_reply_to_outbound_url():
    process_fn = AsyncMock(return_value="It is 3:30 PM.")
    fetch_fn = AsyncMock(return_value={"ok": True})
    bridge = ChannelBridge(
        process_fn=process_fn,
        fetch_fn=fetch_fn,
        outbound_url="http://localhost:3000/agent/reply",
    )

    await bridge.handle_inbound({
        "channel": "telegram", "sender_id": "u1", "text": "hi",
    })

    fetch_fn.assert_called_once()
    kwargs = fetch_fn.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["url"] == "http://localhost:3000/agent/reply"
    body = kwargs["json"]
    assert body["text"] == "It is 3:30 PM."
    assert body["channel"] == "telegram"
    assert body["sender_id"] == "u1"


async def test_outbound_post_includes_bearer_token_when_api_key_set():
    fetch_fn = AsyncMock(return_value={"ok": True})
    bridge = ChannelBridge(
        process_fn=AsyncMock(return_value="ok"),
        fetch_fn=fetch_fn,
        api_key="secret-token",
    )

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "hi"})

    headers = fetch_fn.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret-token"


async def test_outbound_post_omits_auth_header_when_no_api_key():
    fetch_fn = AsyncMock(return_value={"ok": True})
    bridge = ChannelBridge(
        process_fn=AsyncMock(return_value="ok"),
        fetch_fn=fetch_fn,
        api_key="",
    )

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "hi"})

    headers = fetch_fn.call_args.kwargs["headers"]
    assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# Slice 2 — session buffer per (channel, sender_id)
# ---------------------------------------------------------------------------

async def test_history_records_user_and_assistant_turns():
    process_fn = AsyncMock(return_value="hello back")
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=AsyncMock())

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "hello"})

    history = bridge.get_history("tg:u1")
    assert len(history) == 2
    assert history[0] == {"role": "user", "text": "hello"}
    assert history[1] == {"role": "assistant", "text": "hello back"}


async def test_sessions_isolated_per_sender():
    process_fn = AsyncMock(return_value="ok")
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=AsyncMock())

    await bridge.handle_inbound({"channel": "tg", "sender_id": "alice", "text": "hi-alice"})
    await bridge.handle_inbound({"channel": "tg", "sender_id": "bob", "text": "hi-bob"})

    alice = bridge.get_history("tg:alice")
    bob = bridge.get_history("tg:bob")
    assert alice[0]["text"] == "hi-alice"
    assert bob[0]["text"] == "hi-bob"


async def test_sessions_isolated_per_channel():
    process_fn = AsyncMock(return_value="ok")
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=AsyncMock())

    await bridge.handle_inbound({"channel": "telegram", "sender_id": "u1", "text": "tg-msg"})
    await bridge.handle_inbound({"channel": "discord", "sender_id": "u1", "text": "dc-msg"})

    assert bridge.get_history("telegram:u1")[0]["text"] == "tg-msg"
    assert bridge.get_history("discord:u1")[0]["text"] == "dc-msg"


async def test_process_fn_receives_history_on_followup():
    process_fn = AsyncMock(side_effect=["first reply", "second reply"])
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=AsyncMock())

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "first"})
    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "second"})

    second_call = process_fn.call_args_list[1]
    history_arg = second_call.args[1]
    texts = [h["text"] for h in history_arg]
    assert "first" in texts
    assert "first reply" in texts


async def test_history_limit_truncates_oldest_entries():
    process_fn = AsyncMock(return_value="ok")
    bridge = ChannelBridge(
        process_fn=process_fn, fetch_fn=AsyncMock(), history_limit=4,
    )

    for i in range(10):
        await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": f"m{i}"})

    history = bridge.get_history("tg:u1")
    assert len(history) == 4
    # The newest entries are kept
    assert history[-1]["text"] == "ok"
    assert history[-2]["text"] == "m9"


# ---------------------------------------------------------------------------
# Slice 3 — error handling
# ---------------------------------------------------------------------------

async def test_process_fn_exception_sends_error_reply_to_channel():
    process_fn = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    fetch_fn = AsyncMock(return_value={"ok": True})
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=fetch_fn)

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "hi"})

    fetch_fn.assert_called_once()
    body = fetch_fn.call_args.kwargs["json"]
    assert "sorry" in body["text"].lower()


async def test_outbound_failure_does_not_raise():
    """If OpenClaw is unreachable when posting a reply, the bridge logs and
    keeps running — it must not crash the inbound loop."""
    fetch_fn = AsyncMock(side_effect=ConnectionError("OpenClaw down"))
    bridge = ChannelBridge(
        process_fn=AsyncMock(return_value="ok"),
        fetch_fn=fetch_fn,
    )
    # No exception should escape
    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "hi"})


async def test_empty_text_is_ignored():
    process_fn = AsyncMock()
    fetch_fn = AsyncMock()
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=fetch_fn)

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": ""})
    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "   "})
    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1"})  # no text key

    process_fn.assert_not_called()
    fetch_fn.assert_not_called()


async def test_missing_sender_id_is_ignored():
    process_fn = AsyncMock()
    fetch_fn = AsyncMock()
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=fetch_fn)

    await bridge.handle_inbound({"channel": "tg", "text": "hi"})

    process_fn.assert_not_called()
    fetch_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Slice 4 — start() / stop() drive an inbound WebSocket loop
# ---------------------------------------------------------------------------

async def test_start_connects_to_configured_ws_url():
    fake_ws = FakeWS()
    ws_connect_fn = AsyncMock(return_value=fake_ws)
    bridge = ChannelBridge(
        process_fn=AsyncMock(),
        fetch_fn=AsyncMock(),
        ws_connect_fn=ws_connect_fn,
        ws_url="ws://example.com/agent/stream",
    )

    task = asyncio.create_task(bridge.start())
    await asyncio.sleep(0.05)
    await bridge.stop()
    await asyncio.wait_for(task, timeout=1.0)

    ws_connect_fn.assert_called_once()
    # URL passed in either positional or keyword form
    call = ws_connect_fn.call_args
    assert (call.args and call.args[0] == "ws://example.com/agent/stream") or \
           call.kwargs.get("url") == "ws://example.com/agent/stream"


async def test_ws_inbound_message_drives_process_fn_and_reply():
    inbound = json.dumps({"channel": "telegram", "sender_id": "u1", "text": "hello"})
    fake_ws = FakeWS(messages=[inbound])

    process_fn = AsyncMock(return_value="hi back")
    fetch_fn = AsyncMock(return_value={"ok": True})
    ws_connect_fn = AsyncMock(return_value=fake_ws)

    bridge = ChannelBridge(
        process_fn=process_fn,
        fetch_fn=fetch_fn,
        ws_connect_fn=ws_connect_fn,
    )

    task = asyncio.create_task(bridge.start())
    # Give the loop time to consume the message
    for _ in range(20):
        if process_fn.call_count > 0:
            break
        await asyncio.sleep(0.02)

    await bridge.stop()
    await asyncio.wait_for(task, timeout=1.0)

    process_fn.assert_called_once()
    fetch_fn.assert_called_once()
    body = fetch_fn.call_args.kwargs["json"]
    assert body["text"] == "hi back"


async def test_stop_closes_ws_connection():
    fake_ws = FakeWS()
    ws_connect_fn = AsyncMock(return_value=fake_ws)
    bridge = ChannelBridge(
        process_fn=AsyncMock(),
        fetch_fn=AsyncMock(),
        ws_connect_fn=ws_connect_fn,
    )

    task = asyncio.create_task(bridge.start())
    await asyncio.sleep(0.05)
    await bridge.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert fake_ws.closed is True


async def test_start_is_graceful_when_ws_connect_fails(caplog):
    """If OpenClaw is not running, the bridge logs a warning and exits cleanly
    rather than crashing Cerebral. Same graceful-degradation pattern as the
    audio pipeline and TTS engine.
    """
    ws_connect_fn = AsyncMock(side_effect=ConnectionRefusedError("OpenClaw down"))
    bridge = ChannelBridge(
        process_fn=AsyncMock(),
        fetch_fn=AsyncMock(),
        ws_connect_fn=ws_connect_fn,
    )

    with caplog.at_level(logging.WARNING):
        await bridge.start()  # should return, not raise

    assert any("OpenClaw" in rec.message or "openclaw" in rec.message.lower()
               for rec in caplog.records)


async def test_malformed_json_on_ws_is_skipped():
    """Garbage on the WebSocket should not crash the loop."""
    fake_ws = FakeWS(messages=["not valid json {", json.dumps({
        "channel": "tg", "sender_id": "u1", "text": "ok"
    })])
    process_fn = AsyncMock(return_value="reply")
    ws_connect_fn = AsyncMock(return_value=fake_ws)

    bridge = ChannelBridge(
        process_fn=process_fn,
        fetch_fn=AsyncMock(),
        ws_connect_fn=ws_connect_fn,
    )

    task = asyncio.create_task(bridge.start())
    for _ in range(20):
        if process_fn.call_count > 0:
            break
        await asyncio.sleep(0.02)
    await bridge.stop()
    await asyncio.wait_for(task, timeout=1.0)

    # Garbage skipped, the valid message processed
    process_fn.assert_called_once()


# ---------------------------------------------------------------------------
# Slice 5 — running property + reset_session
# ---------------------------------------------------------------------------

async def test_running_property_reflects_lifecycle():
    fake_ws = FakeWS()
    bridge = ChannelBridge(
        process_fn=AsyncMock(),
        fetch_fn=AsyncMock(),
        ws_connect_fn=AsyncMock(return_value=fake_ws),
    )

    assert bridge.running is False
    task = asyncio.create_task(bridge.start())
    await asyncio.sleep(0.05)
    assert bridge.running is True
    await bridge.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert bridge.running is False


async def test_reset_session_clears_history_for_one_session():
    process_fn = AsyncMock(return_value="ok")
    bridge = ChannelBridge(process_fn=process_fn, fetch_fn=AsyncMock())

    await bridge.handle_inbound({"channel": "tg", "sender_id": "u1", "text": "hi"})
    await bridge.handle_inbound({"channel": "tg", "sender_id": "u2", "text": "yo"})

    bridge.reset_session("tg:u1")

    assert bridge.get_history("tg:u1") == []
    assert bridge.get_history("tg:u2") != []
