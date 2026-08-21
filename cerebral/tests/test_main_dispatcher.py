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


# ── jobs scan lane (#403) ────────────────────────────────────────────────────
# jobs_score_shortlist previously awaited ~100 inline LLM calls inside
# _handle_message, so it blocked that connection's entire read loop -- later
# messages on the SAME connection (model changes, pane fetches, interrupts)
# queued invisibly until scoring finished. The fix backgrounds the two known-
# long handlers (jobs_fetch_postings, jobs_score_shortlist) the same way the
# panel-apply lane (#413/#417) already backgrounds jobs_apply_start/submit --
# every other one of the ~50 dispatcher branches keeps its prior sequential,
# await-before-next-frame behavior unchanged.

async def test_slow_jobs_score_does_not_block_next_message(dispatcher_rig, monkeypatch):
    """The reported #403 symptom, reproduced directly: a parked
    jobs_score_shortlist tool call must not stop a later message on the same
    connection from dispatching."""
    from cerebral.mcp.orchestrator import ToolResult

    release = asyncio.Event()
    calls: list[str] = []

    async def slow_call_tool(name, args):
        calls.append(name)
        await release.wait()
        return ToolResult(content="{}")

    main_mod = dispatcher_rig.module
    monkeypatch.setattr(main_mod._orc, "call_tool", slow_call_tool)
    monkeypatch.setattr(main_mod, "_jobs_update_event", lambda: {"type": "jobs_update"})

    ws = FakeWebSocket([
        json.dumps({"type": "jobs_score_shortlist"}),
        json.dumps({"type": "list_voices"}),
    ])

    task = asyncio.create_task(main_mod._ws_handler(ws))
    # Let the scoring branch spawn its background task and park on the tool
    # call, then let the read loop pick up the second frame.
    for _ in range(3):
        await asyncio.sleep(0)

    assert calls == ["jobs_score_shortlist"], "scoring tool call started"
    assert any(e.get("type") == "voices_list" for e in dispatcher_rig.broadcasts), (
        "list_voices must have dispatched on the same connection while "
        "scoring is still parked -- this is the bug the issue reports"
    )
    assert not any(e.get("type") == "jobs_update" for e in dispatcher_rig.broadcasts), (
        "scoring hasn't finished yet, so no jobs_update should exist"
    )

    release.set()
    await asyncio.wait_for(task, timeout=1.0)
    await asyncio.sleep(0)  # let the background task's finally-broadcast land
    assert any(e.get("type") == "jobs_update" for e in dispatcher_rig.broadcasts)


async def test_non_jobs_handlers_still_process_sequentially(dispatcher_rig, monkeypatch):
    """Ordering guarantee this fix promises: only jobs_fetch_postings /
    jobs_score_shortlist are exempted from the sequential lane. Every other
    message type must still block the read loop until it completes, exactly
    as before -- proven here with probe_models, an unrelated slow handler."""
    order: list[str] = []
    gate = asyncio.Event()

    async def slow_probe_enabled():
        order.append("probe-start")
        await gate.wait()
        order.append("probe-end")
        return {}

    main_mod = dispatcher_rig.module
    monkeypatch.setattr(main_mod._router, "probe_enabled", slow_probe_enabled)

    ws = FakeWebSocket([
        json.dumps({"type": "probe_models"}),
        json.dumps({"type": "list_voices"}),
    ])

    task = asyncio.create_task(main_mod._ws_handler(ws))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert order == ["probe-start"], "probe_models must still be mid-flight"
    assert not any(e.get("type") == "voices_list" for e in dispatcher_rig.broadcasts), (
        "list_voices must wait behind the still-sequential probe_models "
        "handler -- unlike the jobs lane, this branch was never backgrounded"
    )

    gate.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert order == ["probe-start", "probe-end"]
    assert any(e.get("type") == "voices_list" for e in dispatcher_rig.broadcasts)


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


# ---------------------------------------------------------------------------
# H3-S1 -- derive_model_context() single assembly seam (ADR-0022 S1)
# ---------------------------------------------------------------------------

@pytest.fixture
def derive_ctx_rig(monkeypatch):
    """Stub _memory_preamble / _conversation_context (both already covered by
    their own tests) so derive_model_context's COMPOSITION is what's under
    test here, plus the conversation-store pieces the invariant re-queries."""
    import cerebral.main as main_mod

    calls = {"memory": [], "history": []}

    async def fake_preamble(query):
        calls["memory"].append(query)
        return "PREAMBLE:"

    async def fake_history(profile_id, **kw):
        calls["history"].append(profile_id)
        return "HISTORY:"

    monkeypatch.setattr(main_mod, "_memory_preamble", fake_preamble)
    monkeypatch.setattr(main_mod, "_conversation_context", fake_history)
    monkeypatch.setattr(main_mod, "_resolve_active_thread_id", lambda profile_id: 1)

    class Rig:
        module = main_mod
        memory_calls = calls["memory"]
        history_calls = calls["history"]

        def set_last_logged_text(self, text):
            monkeypatch.setattr(
                main_mod, "_conversation", _FakeConversationStore([_FakeTurn("user_text", text)])
            )

    return Rig()


async def test_derive_model_context_composes_history_preamble_transcript(derive_ctx_rig):
    out = await derive_ctx_rig.module.derive_model_context(1, "hello there")
    assert out == "HISTORY:PREAMBLE:hello there"
    assert derive_ctx_rig.history_calls == [1]
    assert derive_ctx_rig.memory_calls == ["hello there"]


async def test_derive_model_context_no_profile_skips_history(derive_ctx_rig):
    out = await derive_ctx_rig.module.derive_model_context(None, "hi")
    assert out == "PREAMBLE:hi"          # no HISTORY: prefix
    assert derive_ctx_rig.history_calls == []   # _conversation_context never called


async def test_invariant_off_by_default(derive_ctx_rig):
    assert derive_ctx_rig.module._ASSERT_CONTEXT_INVARIANT is False


async def test_invariant_passes_when_transcript_matches_logged_turn(derive_ctx_rig, monkeypatch):
    monkeypatch.setattr(derive_ctx_rig.module, "_ASSERT_CONTEXT_INVARIANT", True)
    derive_ctx_rig.set_last_logged_text("hello there")
    # Must not raise -- the transcript matches what was actually logged.
    await derive_ctx_rig.module.derive_model_context(1, "hello there")


async def test_invariant_fires_when_transcript_diverges_from_log(derive_ctx_rig, monkeypatch):
    monkeypatch.setattr(derive_ctx_rig.module, "_ASSERT_CONTEXT_INVARIANT", True)
    derive_ctx_rig.set_last_logged_text("something else entirely")
    with pytest.raises(AssertionError):
        await derive_ctx_rig.module.derive_model_context(1, "hello there")


# ---------------------------------------------------------------------------
# feat/model-status-dot -- active health probe broadcast
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# #810 -- self_dev_pr_merge IPC dispatcher case (ADR-0015 amendment,
# "in-chat pending-review card, human-click-only merge")
# ---------------------------------------------------------------------------

class _FakeSelfDevPlugin:
    """Stubs SelfDevPlugin._merge/_load -- never real git/gh/network."""

    def __init__(self, merge_error=None, load_is_error=False, load_content="{}"):
        self.merge_calls = []
        self.load_calls = []
        self._merge_error = merge_error
        self._load_is_error = load_is_error
        self._load_content = load_content

    def _merge(self, pr_url):
        self.merge_calls.append(pr_url)
        if self._merge_error is not None:
            raise self._merge_error

    async def _load(self, args):
        from cerebral.mcp.orchestrator import ToolResult
        self.load_calls.append(args)
        return ToolResult(content=self._load_content, is_error=self._load_is_error)


@pytest.fixture
def self_dev_merge_rig(monkeypatch):
    import cerebral.main as main_mod

    broadcasts: list[dict] = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)

    class Rig:
        module = main_mod
        broadcasts_list = broadcasts

        def install_plugin(self, plugin):
            monkeypatch.setitem(main_mod._orc._plugins, "self_dev", plugin)
            return plugin

    return Rig()


async def test_self_dev_pr_merge_calls_merge_then_load(self_dev_merge_rig):
    """The dispatcher case reuses SelfDevPlugin's own _merge/_load -- it
    must not reimplement gh merge/pull itself."""
    plugin = self_dev_merge_rig.install_plugin(_FakeSelfDevPlugin())
    pr_url = "https://github.com/iggyghub/OpenMind/pull/810"

    await self_dev_merge_rig.module._handle_message({
        "type": "self_dev_pr_merge", "data": {"pr_url": pr_url},
    })

    assert plugin.merge_calls == [pr_url]
    assert plugin.load_calls == [{"pr_url": pr_url}]
    results = [e for e in self_dev_merge_rig.broadcasts_list
               if e.get("type") == "self_dev_pr_merge_result"]
    assert len(results) == 1
    assert results[0]["data"] == {"pr_url": pr_url, "status": "merged"}


async def test_self_dev_pr_merge_failure_broadcasts_error_and_skips_load(self_dev_merge_rig):
    plugin = self_dev_merge_rig.install_plugin(
        _FakeSelfDevPlugin(merge_error=RuntimeError("gh: not mergeable"))
    )
    pr_url = "https://github.com/iggyghub/OpenMind/pull/810"

    await self_dev_merge_rig.module._handle_message({
        "type": "self_dev_pr_merge", "data": {"pr_url": pr_url},
    })

    assert plugin.merge_calls == [pr_url]
    assert plugin.load_calls == [], "must not attempt load after a failed merge"
    results = [e for e in self_dev_merge_rig.broadcasts_list
               if e.get("type") == "self_dev_pr_merge_result"]
    assert len(results) == 1
    assert results[0]["data"]["status"] == "error"
    assert "not mergeable" in results[0]["data"]["error"]


async def test_self_dev_pr_merge_load_failure_still_reports_merged(self_dev_merge_rig):
    """Merge succeeded; only the post-merge pull/restart failed. The card
    must not offer to re-merge an already-merged PR, so status stays
    'merged' with the load failure surfaced separately."""
    plugin = self_dev_merge_rig.install_plugin(
        _FakeSelfDevPlugin(load_is_error=True, load_content="git pull failed")
    )
    pr_url = "https://github.com/iggyghub/OpenMind/pull/810"

    await self_dev_merge_rig.module._handle_message({
        "type": "self_dev_pr_merge", "data": {"pr_url": pr_url},
    })

    results = [e for e in self_dev_merge_rig.broadcasts_list
               if e.get("type") == "self_dev_pr_merge_result"]
    assert results[0]["data"]["status"] == "merged"
    assert results[0]["data"]["load_error"] == "git pull failed"


async def test_self_dev_pr_merge_missing_pr_url_broadcasts_error(self_dev_merge_rig):
    plugin = self_dev_merge_rig.install_plugin(_FakeSelfDevPlugin())

    await self_dev_merge_rig.module._handle_message({
        "type": "self_dev_pr_merge", "data": {},
    })

    assert plugin.merge_calls == []
    results = [e for e in self_dev_merge_rig.broadcasts_list
               if e.get("type") == "self_dev_pr_merge_result"]
    assert results[0]["data"]["status"] == "error"


async def test_self_dev_pr_state_broadcasts_live_state(self_dev_merge_rig):
    class _StatePlugin(_FakeSelfDevPlugin):
        def pr_state(self, pr_url):
            self.state_calls = getattr(self, "state_calls", [])
            self.state_calls.append(pr_url)
            return "MERGED"

    plugin = self_dev_merge_rig.install_plugin(_StatePlugin())
    pr_url = "https://github.com/iggyghub/OpenMind/pull/810"

    await self_dev_merge_rig.module._handle_message({
        "type": "self_dev_pr_state", "data": {"pr_url": pr_url},
    })

    assert plugin.state_calls == [pr_url]
    results = [e for e in self_dev_merge_rig.broadcasts_list
               if e.get("type") == "self_dev_pr_state_result"]
    assert results == [{"type": "self_dev_pr_state_result",
                         "data": {"pr_url": pr_url, "state": "MERGED"}}]


async def test_self_dev_pr_state_failure_is_silent(self_dev_merge_rig):
    """A transient gh/network failure must fail open -- no broadcast, no
    exception, so a still-actionable card is never hidden by mistake."""
    class _BoomPlugin(_FakeSelfDevPlugin):
        def pr_state(self, pr_url):
            raise RuntimeError("gh: rate limited")

    self_dev_merge_rig.install_plugin(_BoomPlugin())

    await self_dev_merge_rig.module._handle_message({
        "type": "self_dev_pr_state", "data": {"pr_url": "https://x/pull/1"},
    })

    assert not any(e.get("type") == "self_dev_pr_state_result"
                   for e in self_dev_merge_rig.broadcasts_list)


async def test_probe_models_broadcasts_health(monkeypatch):
    """probe_models must probe the router and broadcast a models_health event
    carrying the {model_id: reachable} map the status dots render from."""
    import cerebral.main as main_mod

    class _FakeRouter:
        async def probe_enabled(self):
            return {"ollama/x": True, "custom/budd": False}

    broadcasts: list[dict] = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    monkeypatch.setattr(main_mod, "_router", _FakeRouter())
    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)

    await main_mod._handle_message({"type": "probe_models"})

    assert broadcasts == [{
        "type": "models_health",
        "data": {"health": {"ollama/x": True, "custom/budd": False}},
    }]
