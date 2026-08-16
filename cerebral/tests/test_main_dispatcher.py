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


# ── conversation IPC (Issue #185 / ADR-0007) ────────────────────────────────

@pytest.fixture
def conversation_rig(monkeypatch):
    """Stub the LLM/process and capture broadcasts + recorded turns so
    we can assert the dispatcher's user_text_command shape without
    standing up a model router."""
    import cerebral.main as main_mod

    broadcasts: list[dict] = []
    recorded: list[tuple[str, dict]] = []
    processed: list[tuple[str, bool]] = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    async def fake_record_turn(kind, content, **_kw):
        # S14 (#297) introduced an optional ``attachment_ids`` kwarg on
        # _record_turn. The dispatcher tests don't care about attachments
        # so the rig accepts and ignores any keyword extras.
        recorded.append((kind, content))

    async def fake_process_command(text, *, speak=True, thread_model_override=None):
        processed.append((text, speak))

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_record_turn", fake_record_turn)
    monkeypatch.setattr(main_mod, "_process_command", fake_process_command)

    class Rig:
        pass

    rig = Rig()
    rig.module = main_mod
    rig.broadcasts = broadcasts
    rig.recorded = recorded
    rig.processed = processed
    return rig


async def test_user_text_command_records_and_processes(conversation_rig):
    """A typed command records a user_text turn, broadcasts wake so the
    visualiser/state pill activate, and kicks off the same LLM pipeline
    voice uses -- but with speak=False so no TTS fires."""
    await conversation_rig.module._handle_message({
        "type": "user_text_command",
        "data": {"text": "what time is it"},
    })
    # asyncio.create_task fires the coroutine on the running loop; yield
    # one tick so the scheduled task actually runs against our stub.
    import asyncio
    await asyncio.sleep(0)

    assert ("user_text", {"text": "what time is it"}) in conversation_rig.recorded
    assert any(e.get("type") == "wake" for e in conversation_rig.broadcasts)
    assert conversation_rig.processed == [("what time is it", False)]


async def test_user_text_command_ignores_empty(conversation_rig):
    """Whitespace-only or missing text must not spawn a thinking cycle --
    a stray Enter press in the input shouldn't wake Felix."""
    await conversation_rig.module._handle_message({
        "type": "user_text_command", "data": {"text": "   "},
    })
    await conversation_rig.module._handle_message({
        "type": "user_text_command", "data": {},
    })
    assert conversation_rig.recorded == []
    assert conversation_rig.processed == []


async def test_list_conversation_turns_broadcasts_snapshot(conversation_rig, monkeypatch):
    """list_conversation_turns must emit a conversation_turns_data event;
    the renderer treats this as its initial transcript on Main window open."""
    snapshot = {"type": "conversation_turns_data",
                "data": {"profile_id": 1, "turns": []}}
    monkeypatch.setattr(
        conversation_rig.module, "_conversation_turns_event",
        lambda limit=50: snapshot,
    )
    await conversation_rig.module._handle_message({
        "type": "list_conversation_turns", "data": {"limit": 50},
    })
    assert snapshot in conversation_rig.broadcasts


# ── H4-S1 command registry integration ────────────────────────────────────────

@pytest.fixture
def process_cmd_rig(monkeypatch):
    """Stub _broadcast, _record_turn, shortlist_tools, and the ADR-0005
    capability gate so we can test _process_command directly without a real
    router, LLM, or profile/consent state. The gate itself (deny-by-default,
    no active profile in a bare test process) is not what this test is
    checking -- H4-S1's own invariant (matched command -> handler runs,
    router untouched) is; SILENT lets that path execute."""
    import cerebral.main as main_mod
    from cerebral.security import Decision

    broadcasts: list[dict] = []
    record_calls: list[tuple[str, dict]] = []
    shortlist_calls: list = []

    async def fake_broadcast(ev: dict) -> None:
        broadcasts.append(ev)

    async def fake_record(kind: str, content: dict, **_kw: object) -> None:
        record_calls.append((kind, content))

    def fake_shortlist(*args: object, **kwargs: object) -> list:
        shortlist_calls.append(args)
        return []

    async def fake_check_capabilities(*args: object, **kwargs: object) -> Decision:
        return Decision.SILENT

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_record_turn", fake_record)
    monkeypatch.setattr(main_mod, "shortlist_tools", fake_shortlist)
    monkeypatch.setattr(main_mod._orc, "check_capabilities", fake_check_capabilities)

    _broadcasts, _record_calls, _shortlist_calls = broadcasts, record_calls, shortlist_calls

    class Rig:
        module = main_mod
        broadcasts = _broadcasts
        record_calls = _record_calls
        shortlist_calls = _shortlist_calls

    return Rig


async def test_command_registry_bypasses_planner(process_cmd_rig) -> None:
    """H4-S1: matched command must NOT call shortlist_tools / Planner.
    Instead it should execute the handler and record a system event."""
    await process_cmd_rig.module._process_command("restart felix", speak=False)
    await asyncio.sleep(0)  # let scheduled task / handler complete

    assert len(process_cmd_rig.shortlist_calls) == 0, (
        "shortlist_tools must not be called for matched deterministic commands"
    )
    assert any(
        e.get("type") == "restart_felix" for e in process_cmd_rig.broadcasts
    )
    assert any(
        c[0] == "system_event" and c[1].get("event") == "command_executed"
        for c in process_cmd_rig.record_calls
    )


async def test_unmatched_transcript_reaches_planner(process_cmd_rig) -> None:
    """H4-S1 safety invariant: unmatched transcripts must fall through unchanged.
    This is a regression guard to ensure zero behavior change on no match."""
    await process_cmd_rig.module._process_command("hello world", speak=False)
    await asyncio.sleep(0)

    assert len(process_cmd_rig.shortlist_calls) == 1, (
        "unmatched transcripts must still trigger the planner chain"
    )


# ---------------------------------------------------------------------------
# H1-S3 -- oldest-turn summarization (ADR-0021 decision 2b/5)
# ---------------------------------------------------------------------------

class _FakeTurn:
    def __init__(self, kind, text):
        self.kind = kind
        self.content = {"text": text}


class _FakeConversationStore:
    def __init__(self, turns):
        self._turns = turns

    def list_recent_for_thread(self, thread_id, limit):
        return self._turns[-limit:]


class _FakeRouter:
    def __init__(self, complete_return="a concise summary"):
        self.active_model = "fake/model"
        self.complete_calls = []
        self._complete_return = complete_return

    def context_window_for(self, model_id):
        return 1000  # small window so a modest transcript trips the threshold

    async def complete(self, prompt, task_type="chat"):
        self.complete_calls.append((prompt, task_type))
        return self._complete_return


@pytest.fixture
def convo_ctx_rig(monkeypatch):
    """Stub the pieces _conversation_context touches: thread resolution, the
    conversation store, _router, and _record_turn. No real DB/model/net."""
    import cerebral.main as main_mod

    record_calls: list[tuple[str, dict]] = []

    async def fake_record(kind, content, **_kw):
        record_calls.append((kind, content))

    monkeypatch.setattr(main_mod, "_resolve_active_thread_id", lambda profile_id: 1)
    monkeypatch.setattr(main_mod, "_record_turn", fake_record)

    class Rig:
        module = main_mod
        records = record_calls

        def set_turns(self, turns):
            monkeypatch.setattr(main_mod, "_conversation", _FakeConversationStore(turns))

        def set_router(self, router):
            monkeypatch.setattr(main_mod, "_router", router)

    return Rig()


async def test_conversation_context_under_threshold_no_summary(convo_ctx_rig):
    """Few short turns: normal last-N window, no compaction side effect --
    the H1-S3 hook must be a no-op on the common case (H4-S1's own safety
    property, mirrored here: no trigger = zero behaviour change)."""
    turns = [_FakeTurn("user_text", f"hi {i}") for i in range(3)]
    convo_ctx_rig.set_turns(turns)
    convo_ctx_rig.set_router(_FakeRouter())

    out = await convo_ctx_rig.module._conversation_context(profile_id=1, max_turns=8)

    assert convo_ctx_rig.records == []
    assert "hi 0" in out


async def test_conversation_context_over_threshold_summarizes_and_logs(convo_ctx_rig):
    """More turns than the window, and the dropped-oldest text is over the
    compaction threshold: summarize via task_type='quality' and log a
    KIND_SUMMARY turn (ADR-0021 decision 3 + 5)."""
    from cerebral.db.conversation import KIND_SUMMARY

    # 7, not 6: _conversation_context drops the trailing user turn (the
    # live request already recorded before this call), which eats one of
    # these -- 6 survive into `older`, still well over the 1000-token budget.
    old = [_FakeTurn("user_text", "x" * 500) for _ in range(7)]
    recent = [_FakeTurn("user_text", f"recent {i}") for i in range(8)]
    convo_ctx_rig.set_turns(old + recent)
    router = _FakeRouter()
    convo_ctx_rig.set_router(router)

    await convo_ctx_rig.module._conversation_context(profile_id=1, max_turns=8)

    assert len(router.complete_calls) == 1
    assert router.complete_calls[0][1] == "quality"
    summary_records = [c for c in convo_ctx_rig.records if c[0] == KIND_SUMMARY]
    assert len(summary_records) == 1
    assert summary_records[0][1]["text"] == "a concise summary"
