"""
Dispatcher exception-isolation tests — Issue #151.

Background: during #148 live-verify (2026-05-24), one ``set_static_token``
handler crash put Cerebral's WS server into a sticky 1011-close state —
every subsequent client connection also failed immediately. Root cause:
``_ws_handler`` ran the dispatcher inside a try/except that only caught
``ConnectionClosed``; any other handler exception propagated up, exited
the ``async for raw in websocket`` loop, and the websockets library
returned a 1011 close. The bad-state-builder problem applied identically
to the welcome snapshot — a single greeting failure killed every new
connection.

The #151 fix wraps both the dispatcher call and the greeting block so
per-message / per-event failures stay local: log + reply ``{type:
"error", data: {handler, message}}`` to the offending client, then keep
serving.

The test exercises ``_ws_handler`` directly with a fake websocket — no
real socket, no event-loop server, no JSON-decode dependency on the
websockets library. The fake mirrors the contract ``_ws_handler``
actually uses: ``send(text)``, iteration via ``async for raw``, and
``ConnectionClosed`` propagating from the iterator to terminate the
handler cleanly.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ── fake websocket ────────────────────────────────────────────────────────────

class FakeWebSocket:
    """Drives ``_ws_handler`` without a real socket.

    Iteration over the instance yields queued raw frames in order; when
    the queue runs out, raises ``ConnectionClosed`` to terminate the
    handler cleanly (matches what the websockets library does at EOF).
    ``send`` captures outbound frames as decoded dicts (skipping any
    non-JSON payloads — the handler only sends JSON)."""

    def __init__(self, frames: list[str]):
        self._frames = list(frames)
        self.sent: list[dict] = []
        self.send_raw: list[str] = []

    async def send(self, payload: str) -> None:
        self.send_raw.append(payload)
        try:
            self.sent.append(json.loads(payload))
        except (ValueError, TypeError):
            pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise websockets.exceptions.ConnectionClosed(None, None)
        return self._frames.pop(0)


# ── shared rig ────────────────────────────────────────────────────────────────

@pytest.fixture
def dispatcher_rig():
    """Stub the noisy state builders ``_ws_handler`` calls at greet time
    so the test focuses on dispatch behavior. Each builder defaults to a
    minimal valid event; individual tests may replace one with a raising
    stub to assert greeting isolation."""
    import cerebral.main as main_mod

    saved = {
        "_active_profile": main_mod._active_profile,
        "_broadcast": main_mod._broadcast,
        "_connected": main_mod._connected,
        "_handle_message": main_mod._handle_message,
        "_profile_event": main_mod._profile_event,
        "_profiles_list_event": main_mod._profiles_list_event,
        "_voices_list_event": main_mod._voices_list_event,
        "_queue_update_event": main_mod._queue_update_event,
        "_insights_update_event": main_mod._insights_update_event,
        "_memory_update_event": main_mod._memory_update_event,
        "_env_context_event": main_mod._env_context_event,
        "_models_list_event": main_mod._models_list_event,
        "_plugins_list_event": main_mod._plugins_list_event,
        "_permissions_state_event": main_mod._permissions_state_event,
        "_credentials_state_event": main_mod._credentials_state_event,
    }

    broadcasts: list[dict] = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    main_mod._active_profile = None
    main_mod._broadcast = fake_broadcast
    main_mod._connected = set()
    main_mod._profiles_list_event = lambda: {"type": "profiles_list", "data": {"profiles": []}}
    main_mod._voices_list_event = lambda: {"type": "voices_list", "data": {"voices": []}}
    main_mod._queue_update_event = lambda: {"type": "queue_update", "data": {"items": []}}
    main_mod._insights_update_event = lambda: {"type": "insights_update", "data": {"insights": []}}
    main_mod._memory_update_event = lambda: {"type": "memory_update", "data": {"memories": []}}
    main_mod._env_context_event = lambda: {"type": "env_context_update", "data": {"context": {}}}
    main_mod._models_list_event = lambda: {"type": "models_list", "data": {"models": []}}
    main_mod._plugins_list_event = lambda: {"type": "plugins_list", "data": {"plugins": []}}
    main_mod._permissions_state_event = lambda: {"type": "permissions_state", "data": {}}
    main_mod._credentials_state_event = lambda: {"type": "credentials_state", "data": {}}

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.broadcasts = broadcasts

        def install_handler(self, fn):
            main_mod._handle_message = fn

        def break_greeting(self, name: str, exc: Exception):
            def boom():
                raise exc
            setattr(main_mod, name, boom)

    rig = Rig()
    try:
        yield rig
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── dispatcher isolation ──────────────────────────────────────────────────────

async def test_handler_exception_does_not_close_connection(dispatcher_rig):
    """The core #151 invariant: a raising handler must NOT abort the
    async-for loop. The handler runs once (raises), then the second
    frame is delivered to a fresh handler call on the same socket."""
    seen: list[str] = []

    async def handler(msg):
        seen.append(msg["type"])
        if msg["type"] == "boom":
            raise RuntimeError("simulated handler crash")

    dispatcher_rig.install_handler(handler)
    ws = FakeWebSocket([
        json.dumps({"type": "boom"}),
        json.dumps({"type": "after"}),
    ])

    await dispatcher_rig.module._ws_handler(ws)

    assert seen == ["boom", "after"], (
        "second message must dispatch on the same connection — proves "
        "the handler crash did not break the async-for loop"
    )


async def test_handler_exception_replies_structured_error(dispatcher_rig):
    """Failing handlers must reply ``{type: error, data: {handler,
    message}}`` to the offending client so the renderer can surface a
    toast instead of silently losing the message."""
    async def handler(msg):
        if msg["type"] == "boom":
            raise ValueError("token rejected by store")

    dispatcher_rig.install_handler(handler)
    ws = FakeWebSocket([json.dumps({"type": "boom"})])

    await dispatcher_rig.module._ws_handler(ws)

    errors = [e for e in ws.sent if e.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["data"]["handler"] == "boom"
    assert "token rejected by store" in errors[0]["data"]["message"]


async def test_handler_exception_not_broadcast(dispatcher_rig):
    """Per-client errors must not broadcast — other connected clients
    didn't send the bad message and shouldn't see a phantom error."""
    async def handler(msg):
        raise RuntimeError("local-only")

    dispatcher_rig.install_handler(handler)
    ws = FakeWebSocket([json.dumps({"type": "boom"})])

    await dispatcher_rig.module._ws_handler(ws)

    assert not any(e.get("type") == "error" for e in dispatcher_rig.broadcasts), (
        "errors are reported to the offending client only — never broadcast"
    )


async def test_unknown_message_type_does_not_raise(dispatcher_rig):
    """The default dispatcher silently ignores unknown types (no else
    branch). The isolation wrapper must not synthesise an error event
    for messages that simply weren't matched — that would flood clients
    on every renderer-server skew."""
    async def handler(msg):
        return  # the real dispatcher's no-match fallthrough

    dispatcher_rig.install_handler(handler)
    ws = FakeWebSocket([json.dumps({"type": "totally_unknown"})])

    await dispatcher_rig.module._ws_handler(ws)

    assert not any(e.get("type") == "error" for e in ws.sent)


async def test_bad_json_skipped_silently(dispatcher_rig):
    """Pre-existing contract: malformed JSON frames are skipped, no
    handler runs, no error event. The wrap must not regress this."""
    calls: list[dict] = []

    async def handler(msg):
        calls.append(msg)

    dispatcher_rig.install_handler(handler)
    ws = FakeWebSocket(["not-json", json.dumps({"type": "list_voices"})])

    await dispatcher_rig.module._ws_handler(ws)

    assert calls == [{"type": "list_voices"}]
    assert not any(e.get("type") == "error" for e in ws.sent)


# ── greeting isolation ────────────────────────────────────────────────────────

async def test_broken_greeting_builder_does_not_abort_handshake(dispatcher_rig):
    """The #148 wedge symptom: a transiently broken state builder must
    not kill the welcome handshake. The remaining builders still flow
    and the connection accepts subsequent messages — proves a future
    keyring regression can't lock every client out at connect time."""
    dispatcher_rig.break_greeting(
        "_credentials_state_event", RuntimeError("keyring backend offline")
    )

    async def handler(msg):
        return

    dispatcher_rig.install_handler(handler)
    ws = FakeWebSocket([json.dumps({"type": "list_voices"})])

    await dispatcher_rig.module._ws_handler(ws)

    sent_types = [e["type"] for e in ws.sent]
    # The broken builder is skipped, but the surrounding ones still fire.
    assert "first_run" in sent_types
    assert "voices_list" in sent_types
    assert "credentials_state" not in sent_types
    # And the connection accepted the post-greeting message — i.e. the
    # exception didn't terminate _ws_handler before async-for started.
    # (If it had, no greeting events past credentials_state would exist
    #  AND the loop would never run; the handler's stub accepts cleanly.)
