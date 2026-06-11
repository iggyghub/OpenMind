"""Tests for plugins/openclaw_channels.py -- Issue #168.

The plugin spawns ``openclaw mcp serve`` as a stdio MCP subprocess and
forwards nine channel-bridge tools to Cerebral's orchestrator, plus
drives a background events_wait subscriber loop. None of the side
effects (subprocess spawn, MCP session) are exercised here -- the test
fakes the entire MCP session via constructor injection. No real
subprocess, no real gateway.

The plugin lives at ``plugins/openclaw_channels.py``; tests load it via
spec_from_file_location so the import path doesn't depend on ``plugins``
being on ``sys.path`` (matches the orchestrator's own discovery posture
-- see Issue #153).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import pytest


# ---------------------------------------------------------------------------
# Plugin loader -- mirror orchestrator-style importlib loading
# ---------------------------------------------------------------------------

def _load_plugin_module():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "openclaw_channels.py"
    spec = importlib.util.spec_from_file_location(
        "openmind_plugin_openclaw_channels", plugin_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Bind once; the module is stateless except for two module-level setters
# (set_token_provider / set_inbound_callback) which tests reset explicitly.
PLUGIN_MOD = _load_plugin_module()


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear the module-level setters between tests so order-dependent
    tests don't leak state into one another."""
    PLUGIN_MOD.set_token_provider(lambda: None)
    PLUGIN_MOD.set_inbound_callback(_unused_callback)  # type: ignore[arg-type]
    yield
    PLUGIN_MOD.set_token_provider(lambda: None)
    PLUGIN_MOD.set_inbound_callback(_unused_callback)  # type: ignore[arg-type]


async def _unused_callback(transcript: str, history: list[dict]) -> str:
    return "<unused>"


# ---------------------------------------------------------------------------
# Fake MCP session -- the only side-effect surface the plugin uses
# ---------------------------------------------------------------------------

class _FakeContent(SimpleNamespace):
    """A minimal MCP TextContent shape -- ``type='text', text='...'``."""


class _FakeResult(SimpleNamespace):
    """A minimal MCP CallToolResult shape.

    Exposes ``content`` (list of TextContent-like), ``isError`` (bool),
    and ``structuredContent`` (dict or None). The plugin reads these via
    duck-typing so a plain SimpleNamespace is enough.
    """


def _text_result(payload: Any, *, is_error: bool = False) -> _FakeResult:
    """Build a CallToolResult whose single text item is ``json.dumps(payload)``."""
    return _FakeResult(
        content=[_FakeContent(type="text", text=json.dumps(payload))],
        isError=is_error,
        structuredContent=None,
    )


def _error_result(message: str) -> _FakeResult:
    return _FakeResult(
        content=[_FakeContent(type="text", text=message)],
        isError=True,
        structuredContent=None,
    )


class FakeSession:
    """The slice of the MCP ClientSession the plugin uses, hand-rolled.

    Constructor accepts:
      - ``events_queue``: list of (cursor, event_dict) pairs the next
        events_wait calls return. After exhaustion, events_wait returns
        an empty payload (mimics timeout).
      - ``send_handler``: optional callable invoked on messages_send.
        Defaults to recording the call and returning empty-OK.
      - ``read_handler``: optional callable invoked on messages_read.
      - ``call_recorder``: list to append (name, args) tuples to.
    """

    def __init__(
        self,
        *,
        events_queue: Optional[list[dict]] = None,
        send_handler=None,
        read_handler=None,
        call_recorder: Optional[list] = None,
        events_wait_error: Optional[Exception] = None,
        events_wait_results: Optional[list[_FakeResult]] = None,
    ) -> None:
        self._events: list[dict] = list(events_queue or [])
        self._send_handler = send_handler
        self._read_handler = read_handler
        self._calls = call_recorder if call_recorder is not None else []
        self._events_wait_error = events_wait_error
        self._events_wait_results = list(events_wait_results or [])
        # Track when call_tool gets cancelled so tests can assert clean stop.
        self.events_wait_calls = 0
        self.send_calls: list[dict] = []
        self.read_calls: list[dict] = []
        self.initialize_calls = 0

    async def initialize(self) -> Any:
        self.initialize_calls += 1
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._calls.append((name, dict(arguments)))
        if name == "events_wait":
            self.events_wait_calls += 1
            if self._events_wait_error is not None:
                err = self._events_wait_error
                self._events_wait_error = None  # raise once
                raise err
            if self._events_wait_results:
                return self._events_wait_results.pop(0)
            if self._events:
                evt = self._events.pop(0)
                return _text_result({"events": [evt]})
            # No more events -- emulate long-poll timeout with a short
            # sleep so the loop can be cancelled cleanly.
            await asyncio.sleep(0.01)
            return _text_result({"events": []})
        if name == "messages_send":
            self.send_calls.append(dict(arguments))
            if self._send_handler is not None:
                return self._send_handler(arguments)
            return _text_result({"ok": True})
        if name == "messages_read":
            self.read_calls.append(dict(arguments))
            if self._read_handler is not None:
                return self._read_handler(arguments)
            return _text_result({"messages": []})
        # Default echo for the seven non-bridge tools.
        return _text_result({"echo": {"tool": name, "args": arguments}})


def _session_factory(session: FakeSession):
    """Wrap a FakeSession in the async-context-manager shape the plugin
    expects from its session_factory."""

    @contextlib.asynccontextmanager
    async def factory():
        await session.initialize()
        yield session

    return factory


def _failing_session_factory(exc: Exception):
    """A session_factory that raises on entry -- simulates the spawn
    failure / MCP-handshake failure paths."""

    @contextlib.asynccontextmanager
    async def factory():
        raise exc
        yield  # unreachable; satisfies asynccontextmanager typing

    return factory


# ---------------------------------------------------------------------------
# Token provider
# ---------------------------------------------------------------------------

class _StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def current(self) -> Optional[str]:
        return self._token or None


def _make_plugin(
    *,
    token: str = "test-token-redacted",
    session: Optional[FakeSession] = None,
    inbound_callback=None,
    history_limit: int = 16,
    session_factory=None,
):
    plugin_cls = PLUGIN_MOD.OpenClawChannelsPlugin
    return plugin_cls(
        token_provider=_StaticTokenProvider(token),
        session_factory=session_factory or (
            _session_factory(session) if session is not None else None
        ),
        inbound_callback=inbound_callback,
        history_limit=history_limit,
        events_wait_timeout_ms=50,
        error_backoff_seconds=0.02,
    )


# ===========================================================================
# Tool surface
# ===========================================================================

def test_list_tools_returns_nine_prefixed_tools():
    plugin = _make_plugin(session=FakeSession())
    tools = plugin.list_tools()
    names = [t.name for t in tools]
    assert len(names) == 9
    for name in names:
        assert name.startswith("openclaw_"), name
    # Every upstream tool covered, namespaced.
    expected = {
        "openclaw_conversations_list",
        "openclaw_conversation_get",
        "openclaw_messages_read",
        "openclaw_attachments_fetch",
        "openclaw_events_poll",
        "openclaw_events_wait",
        "openclaw_messages_send",
        "openclaw_permissions_list_open",
        "openclaw_permissions_respond",
    }
    assert set(names) == expected


def test_permissions_respond_is_marked_irreversible():
    plugin = _make_plugin(session=FakeSession())
    tools = {t.name: t for t in plugin.list_tools()}
    assert tools["openclaw_permissions_respond"].irreversible is True
    # The other eight are reversible by default.
    for name, t in tools.items():
        if name != "openclaw_permissions_respond":
            assert t.irreversible is False, name


async def test_call_tool_forwards_to_upstream_name():
    """openclaw_messages_send → session.call_tool("messages_send", args)."""
    recorded: list = []
    session = FakeSession(call_recorder=recorded)
    plugin = _make_plugin(session=session)

    result = await plugin.call_tool(
        "openclaw_messages_send",
        {"session_key": "telegram:u1", "text": "hi"},
    )
    assert result.is_error is False
    # The first recorded call is messages_send with the original args.
    assert recorded[0] == (
        "messages_send",
        {"session_key": "telegram:u1", "text": "hi"},
    )


async def test_call_tool_rejects_unknown_tool_name():
    plugin = _make_plugin(session=FakeSession())
    result = await plugin.call_tool("openclaw_bogus", {})
    assert result.is_error is True
    assert "Unknown tool" in result.content


async def test_call_tool_surfaces_upstream_error_as_is_error():
    """Upstream isError=True -> ToolResult.is_error=True with scrubbed text."""
    class FailingSession(FakeSession):
        async def call_tool(self, name, arguments):  # type: ignore[override]
            await super().call_tool(name, arguments)
            return _error_result("scope upgrade pending approval")
    session = FailingSession()
    plugin = _make_plugin(session=session)
    result = await plugin.call_tool("openclaw_conversations_list", {})
    assert result.is_error is True
    assert "scope upgrade" in result.content


# ===========================================================================
# Token resolution
# ===========================================================================

async def test_call_tool_with_unwired_token_provider_returns_error():
    plugin_cls = PLUGIN_MOD.OpenClawChannelsPlugin
    plugin = plugin_cls(
        token_provider=None,  # no constructor override
        session_factory=None,  # no session factory override
    )
    # Module-level factory returns None too (fixture default).
    result = await plugin.call_tool("openclaw_conversations_list", {})
    assert result.is_error is True
    assert ("token provider not wired" in result.content
            or "no gateway token" in result.content)


async def test_token_scrubbed_from_error_messages():
    """Scrub posture -- if an exception text echoes the token, it's masked."""
    secret = "SECRET-TOKEN-DO-NOT-LEAK-12345"
    class LeakySession(FakeSession):
        async def call_tool(self, name, arguments):  # type: ignore[override]
            raise RuntimeError(f"failed talking to gateway with token={secret}")
    plugin = _make_plugin(token=secret, session=LeakySession())
    result = await plugin.call_tool("openclaw_conversations_list", {})
    assert result.is_error is True
    assert secret not in result.content
    assert "***" in result.content


# ===========================================================================
# Subscriber lifecycle + events_wait loop
# ===========================================================================

async def _drain_until(predicate, *, timeout: float = 1.0, poll: float = 0.01):
    """Sleep in small increments until ``predicate()`` is true or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return False


async def test_start_subscriber_drives_events_wait_loop():
    session = FakeSession()
    plugin = _make_plugin(
        session=session,
        inbound_callback=_unused_callback,
    )
    await plugin.start_subscriber()
    try:
        ok = await _drain_until(lambda: session.events_wait_calls >= 2)
        assert ok, "subscriber didn't poll events_wait at least twice"
    finally:
        await plugin.stop_subscriber()


async def test_event_drives_callback_and_messages_send_reply():
    """Inbound event -> process_fn -> messages_send round-trip."""
    callback_invocations: list[tuple[str, list[dict]]] = []

    async def callback(text: str, history: list[dict]) -> str:
        callback_invocations.append((text, list(history)))
        return f"Felix: I heard '{text}'"

    session = FakeSession(events_queue=[
        {"cursor": 1, "session_key": "telegram:alice", "text": "hello Felix"},
    ])
    plugin = _make_plugin(session=session, inbound_callback=callback)

    await plugin.start_subscriber()
    try:
        ok = await _drain_until(lambda: len(session.send_calls) >= 1)
        assert ok, "messages_send never fired"
    finally:
        await plugin.stop_subscriber()

    assert callback_invocations
    text, _ = callback_invocations[0]
    assert text == "hello Felix"
    assert session.send_calls[0] == {
        "session_key": "telegram:alice",
        "text": "Felix: I heard 'hello Felix'",
    }


async def test_event_without_inline_text_falls_back_to_messages_read():
    """When the event lacks ``text``, the plugin reads it via messages_read."""
    callback_invocations: list[str] = []

    async def callback(text: str, history: list[dict]) -> str:
        callback_invocations.append(text)
        return "ok"

    def read_handler(args: dict):
        return _text_result({
            "messages": [{"id": "m1", "text": "fetched-text"}],
        })

    session = FakeSession(
        events_queue=[{"cursor": 1, "session_key": "telegram:u1"}],
        read_handler=read_handler,
    )
    plugin = _make_plugin(session=session, inbound_callback=callback)
    await plugin.start_subscriber()
    try:
        ok = await _drain_until(lambda: len(callback_invocations) >= 1)
        assert ok
    finally:
        await plugin.stop_subscriber()
    assert callback_invocations[0] == "fetched-text"
    assert len(session.read_calls) >= 1


# ===========================================================================
# Per-(channel, sender_id) history buffer -- lifted from ChannelBridge
# ===========================================================================

async def test_history_records_user_and_assistant_turns():
    async def callback(text: str, history: list[dict]) -> str:
        return f"echo: {text}"
    session = FakeSession(events_queue=[
        {"cursor": 1, "session_key": "telegram:u1", "text": "hi"},
    ])
    plugin = _make_plugin(session=session, inbound_callback=callback)
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(session.send_calls) >= 1)
    finally:
        await plugin.stop_subscriber()
    hist = plugin.get_history("telegram:u1")
    assert hist == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "echo: hi"},
    ]


async def test_history_isolated_per_session_key():
    async def callback(text: str, history: list[dict]) -> str:
        return f"reply-to-{text}"
    session = FakeSession(events_queue=[
        {"cursor": 1, "session_key": "telegram:alice", "text": "alice"},
        {"cursor": 2, "session_key": "discord:bob", "text": "bob"},
    ])
    plugin = _make_plugin(session=session, inbound_callback=callback)
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(session.send_calls) >= 2)
    finally:
        await plugin.stop_subscriber()
    alice = plugin.get_history("telegram:alice")
    bob = plugin.get_history("discord:bob")
    assert alice[0]["text"] == "alice"
    assert bob[0]["text"] == "bob"
    assert len(alice) == 2 and len(bob) == 2


async def test_history_truncates_at_limit():
    async def callback(text: str, history: list[dict]) -> str:
        return "ok"
    events = [
        {"cursor": i, "session_key": "telegram:u1", "text": f"m{i}"}
        for i in range(10)
    ]
    session = FakeSession(events_queue=events)
    plugin = _make_plugin(
        session=session,
        inbound_callback=callback,
        history_limit=4,
    )
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(session.send_calls) >= 10)
    finally:
        await plugin.stop_subscriber()
    hist = plugin.get_history("telegram:u1")
    assert len(hist) == 4
    # Newest entries retained.
    assert hist[-1]["text"] == "ok"
    assert hist[-2]["text"] == "m9"


async def test_callback_exception_keeps_loop_running():
    """One bad event doesn't kill the subscriber loop."""
    attempts = 0
    async def callback(text: str, history: list[dict]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("LLM exploded")
        return "ok"

    session = FakeSession(events_queue=[
        {"cursor": 1, "session_key": "telegram:u1", "text": "first"},
        {"cursor": 2, "session_key": "telegram:u1", "text": "second"},
    ])
    plugin = _make_plugin(session=session, inbound_callback=callback)
    await plugin.start_subscriber()
    try:
        ok = await _drain_until(lambda: attempts >= 2)
        assert ok, "loop didn't survive the first exception"
    finally:
        await plugin.stop_subscriber()
    # Both replies attempted (the first uses ERROR_REPLY, the second
    # the real callback return).
    assert len(session.send_calls) >= 2


# ===========================================================================
# Graceful degradation -- subprocess/handshake failures
# ===========================================================================

async def test_start_subscriber_is_graceful_when_session_fails(caplog):
    plugin_cls = PLUGIN_MOD.OpenClawChannelsPlugin
    plugin = plugin_cls(
        token_provider=_StaticTokenProvider("redacted"),
        session_factory=_failing_session_factory(
            ConnectionRefusedError("openclaw mcp serve not found"),
        ),
        inbound_callback=_unused_callback,
        events_wait_timeout_ms=50,
        error_backoff_seconds=0.02,
    )
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
    assert not plugin.subscriber_running
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("gateway unreachable" in m for m in msgs), msgs


async def test_session_context_entered_and_exited_in_same_task():
    """Issue #181: the mcp SDK's stdio_client uses anyio cancel scopes
    that must be exited by the task that entered them. The session
    context manager must therefore be entered AND exited inside the
    same (dedicated host) task, no matter which task drives
    start_subscriber / stop_subscriber."""
    record: dict = {}
    session = FakeSession()

    @contextlib.asynccontextmanager
    async def cm():
        record["enter_task"] = asyncio.current_task()
        try:
            yield session
        finally:
            record["exit_task"] = asyncio.current_task()

    plugin = _make_plugin(
        session_factory=lambda: cm(), inbound_callback=_unused_callback,
    )
    await plugin.start_subscriber()
    await plugin.stop_subscriber()
    assert "exit_task" in record, "session context was never exited"
    assert record["enter_task"] is record["exit_task"]
    # And neither happened in the test task -- the host owns the scope.
    assert record["enter_task"] is not asyncio.current_task()


async def test_stop_subscriber_survives_teardown_exception():
    """Issue #181: a dead ``openclaw mcp serve`` child makes the SDK's
    teardown raise (ExceptionGroup / cancel-scope RuntimeError). That
    must stay contained -- stop_subscriber returns normally and the
    loop is not poisoned."""
    session = FakeSession()

    @contextlib.asynccontextmanager
    async def cm():
        try:
            yield session
        finally:
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task "
                "than it was entered in",
            )

    plugin = _make_plugin(
        session_factory=lambda: cm(), inbound_callback=_unused_callback,
    )
    await plugin.start_subscriber()
    await plugin.stop_subscriber()  # must not raise
    assert plugin.subscriber_running is False
    # The loop is still healthy: unrelated tasks run to completion.
    await asyncio.sleep(0)


async def test_session_invalidate_survives_teardown_exception():
    """Same containment on the _invalidate_session path (events_wait
    raise -> respawn). The first session's teardown raising must not
    prevent the loop from bringing up the second session."""
    first, second = FakeSession(), FakeSession()
    handed_out: list[FakeSession] = []

    @contextlib.asynccontextmanager
    async def cm():
        sess = first if not handed_out else second
        handed_out.append(sess)
        try:
            yield sess
        finally:
            if sess is first:
                raise RuntimeError("athrow(): asynchronous generator "
                                   "is already running")

    first._events_wait_error = OSError("connection reset")

    plugin = _make_plugin(
        session_factory=lambda: cm(), inbound_callback=_unused_callback,
    )
    await plugin.start_subscriber()
    try:
        ok = await _drain_until(lambda: second.events_wait_calls >= 1)
        assert ok, "loop never recovered onto a fresh session"
    finally:
        await plugin.stop_subscriber()


async def test_stop_subscriber_is_idempotent():
    session = FakeSession()
    plugin = _make_plugin(session=session, inbound_callback=_unused_callback)
    await plugin.start_subscriber()
    await plugin.stop_subscriber()
    await plugin.stop_subscriber()  # second call must not raise
    assert plugin.subscriber_running is False


async def test_events_wait_error_logs_and_backs_off(caplog):
    """A raised exception inside events_wait shouldn't kill the loop."""
    raised = [False]
    class FlakeySession(FakeSession):
        async def call_tool(self, name, arguments):  # type: ignore[override]
            if name == "events_wait" and not raised[0]:
                raised[0] = True
                raise OSError("connection reset")
            return await super().call_tool(name, arguments)
    session = FlakeySession()
    plugin = _make_plugin(session=session, inbound_callback=_unused_callback)
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
        try:
            # Allow a few iterations so the recovery path runs.
            await _drain_until(lambda: session.events_wait_calls >= 2)
        finally:
            await plugin.stop_subscriber()
    # The first call raised; the second call succeeded.
    assert session.events_wait_calls >= 2
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("events_wait raised" in m for m in msgs), msgs


async def test_scope_upgrade_warning_logged_once(caplog):
    """A 'scope upgrade pending' isError gets one WARN, not a flood."""
    class ScopeBlockedSession(FakeSession):
        async def call_tool(self, name, arguments):  # type: ignore[override]
            self._calls.append((name, dict(arguments)))
            if name == "events_wait":
                self.events_wait_calls += 1
                return _error_result(
                    "scope upgrade pending approval (requestId: deadbeef)",
                )
            return _text_result({"ok": True})
    session = ScopeBlockedSession()
    plugin = _make_plugin(session=session, inbound_callback=_unused_callback)
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
        try:
            await _drain_until(lambda: session.events_wait_calls >= 3)
        finally:
            await plugin.stop_subscriber()
    msgs = [rec.getMessage() for rec in caplog.records]
    scope_warns = [m for m in msgs if "scope upgrade" in m.lower()]
    # The first scope error logs the actionable WARN, subsequent
    # iterations fall to the generic events_wait branch (rate-limited
    # so the operator log doesn't flood).
    assert any("openclaw devices approve" in m for m in scope_warns)
    actionable = [m for m in scope_warns if "openclaw devices approve" in m]
    assert len(actionable) == 1, msgs


# ===========================================================================
# Issue #172 -- closed-WS scope-upgrade rejection path
#
# The 2026.4.29 gateway does NOT return ``isError=True`` for scope-upgrade
# rejections; it closes the underlying WebSocket. The mcp SDK then raises
# ``McpError("Connection closed")`` on the next read. The actionable WARN
# in the ``_is_error(result)`` branch is unreachable on this path; the
# exception branch has to emit it instead. Pair this with cache
# invalidation of ``_session`` so the next iteration spawns a fresh stdio
# child once the operator approves the scope out-of-band.
# ===========================================================================


def _multi_session_factory(sessions: list[FakeSession]):
    """Return a session_factory that yields sequential ``FakeSession``s on
    each ``_ensure_session`` invocation -- one per call.

    Lets a test prove the cache was invalidated: if the plugin keeps
    reusing the first session, ``factory()`` is only called once and
    later sessions never receive events.
    """
    invocations = {"count": 0}

    @contextlib.asynccontextmanager
    async def factory():
        idx = invocations["count"]
        invocations["count"] += 1
        if idx >= len(sessions):
            # Exhausted -- yield the last one to keep tests stable rather
            # than raise. Real session_factory would respawn each time.
            session = sessions[-1]
        else:
            session = sessions[idx]
        await session.initialize()
        yield session

    factory.invocations = invocations  # type: ignore[attr-defined]
    return factory


async def test_closed_ws_raise_logs_actionable_scope_warn_once(caplog):
    """First events_wait raise emits the actionable scope-upgrade WARN
    naming ``openclaw devices approve --latest`` -- and only once across
    N iterations.

    Repro shape from issue #172: the gateway closes the WebSocket and
    the mcp SDK raises ``McpError`` with message ``"Connection closed"``.
    Before the fix, the loop logged the unhelpful ``events_wait raised:
    Connection closed -- backing off`` on every iteration. After the
    fix, exactly one actionable WARN names the approve command.
    """
    raises_remaining = {"count": 3}

    class ClosedWSSession(FakeSession):
        async def call_tool(self, name, arguments):  # type: ignore[override]
            self._calls.append((name, dict(arguments)))
            if name == "events_wait":
                self.events_wait_calls += 1
                if raises_remaining["count"] > 0:
                    raises_remaining["count"] -= 1
                    # OSError("Connection closed") stands in for the mcp
                    # SDK's McpError(ErrorData(code=CONNECTION_CLOSED,
                    # message="Connection closed")) -- the plugin handles
                    # both via the generic ``except Exception``.
                    raise OSError("Connection closed")
                # Subsequent calls succeed with an empty payload.
                await asyncio.sleep(0.01)
                return _text_result({"events": []})
            return _text_result({"ok": True})

    sessions = [ClosedWSSession() for _ in range(5)]
    factory = _multi_session_factory(sessions)
    plugin = _make_plugin(
        session_factory=factory,
        inbound_callback=_unused_callback,
    )
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
        try:
            # Allow several iterations of the loop. The first three
            # raise, then the loop recovers.
            await _drain_until(
                lambda: sum(s.events_wait_calls for s in sessions) >= 4,
                timeout=2.0,
            )
        finally:
            await plugin.stop_subscriber()

    msgs = [rec.getMessage() for rec in caplog.records]
    # Exactly ONE actionable WARN line names the approve command.
    actionable = [m for m in msgs if "openclaw devices approve --latest" in m]
    assert len(actionable) == 1, f"expected 1 actionable WARN; got {len(actionable)}: {msgs}"
    # The actionable WARN also points at list-pending so the operator
    # can find the requestId (which the SDK doesn't surface).
    assert "list-pending" in actionable[0], actionable[0]
    # The legacy unhelpful "events_wait raised: Connection closed --
    # backing off" line should NOT be repeated for every iteration --
    # the rate-limited path swallows subsequent terse lines into the
    # `_scope_warned` branch.
    terse_dupes = [m for m in msgs if m.startswith(
        "[openclaw_channels] events_wait raised: Connection closed",
    )]
    # At most one terse line per raise AFTER the first; the actionable
    # WARN replaces the terse one on the first raise.
    assert len(terse_dupes) <= 2, terse_dupes


async def test_closed_ws_raise_invalidates_cached_session(caplog):
    """After an events_wait raise, ``_ensure_session`` calls the factory
    again -- the cached dead session is dropped.

    If the cache weren't invalidated, the factory's invocation counter
    would stay at 1 forever and the operator would have to restart
    Cerebral to recover.
    """

    class OneShotSession(FakeSession):
        """Raises ``OSError("Connection closed")`` exactly once on
        events_wait, then never responds again (the dead-WS analogue)."""

        async def call_tool(self, name, arguments):  # type: ignore[override]
            self._calls.append((name, dict(arguments)))
            if name == "events_wait":
                self.events_wait_calls += 1
                if self.events_wait_calls == 1:
                    raise OSError("Connection closed")
                # If the cache wasn't invalidated, the plugin would land
                # here on subsequent iterations and never reach the
                # second session's events_wait.
                await asyncio.sleep(0.01)
                return _text_result({"events": []})
            return _text_result({"ok": True})

    sessions = [OneShotSession(), FakeSession()]
    factory = _multi_session_factory(sessions)
    plugin = _make_plugin(
        session_factory=factory,
        inbound_callback=_unused_callback,
    )
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
        try:
            # Recovery must flow all the way onto the fresh session.
            # (Draining on the factory invocation count alone is racy
            # since the #181 session host added suspension points
            # between context entry and the first poll.)
            ok = await _drain_until(
                lambda: sessions[1].events_wait_calls >= 1,
                timeout=2.0,
            )
            assert ok, (
                "second session never polled events_wait -- cache "
                f"invalidation broken; invocations={factory.invocations['count']}"
            )
            assert factory.invocations["count"] >= 2
        finally:
            await plugin.stop_subscriber()

    # The second (fresh) session should have received at least one
    # events_wait call -- recovery flowed through it.
    assert sessions[1].events_wait_calls >= 1, (
        "second session never polled events_wait -- recovery never reached it"
    )


async def test_closed_ws_recovery_resumes_normal_event_flow():
    """End-to-end: first events_wait raises ``Connection closed``, the
    plugin invalidates the session, the next ``_ensure_session`` brings
    up a fresh session whose events_wait returns a real event, and the
    inbound callback fires normally -- no Cerebral restart required.
    """
    callback_invocations: list[str] = []

    async def callback(text: str, history: list[dict]) -> str:
        callback_invocations.append(text)
        return f"echo: {text}"

    class FirstSessionDead(FakeSession):
        async def call_tool(self, name, arguments):  # type: ignore[override]
            self._calls.append((name, dict(arguments)))
            if name == "events_wait":
                self.events_wait_calls += 1
                raise OSError("Connection closed")
            return _text_result({"ok": True})

    # The second session delivers a real event so the callback fires.
    recovered_session = FakeSession(events_queue=[
        {"cursor": 7, "session_key": "telegram:alice", "text": "post-recovery hello"},
    ])
    sessions: list[FakeSession] = [FirstSessionDead(), recovered_session]
    factory = _multi_session_factory(sessions)

    plugin = _make_plugin(
        session_factory=factory,
        inbound_callback=callback,
    )
    await plugin.start_subscriber()
    try:
        ok = await _drain_until(
            lambda: len(callback_invocations) >= 1,
            timeout=2.0,
        )
        assert ok, "callback never fired -- recovery path broken"
    finally:
        await plugin.stop_subscriber()

    assert callback_invocations == ["post-recovery hello"]
    # And the reply was sent via the second (recovered) session.
    assert recovered_session.send_calls and (
        recovered_session.send_calls[0]["text"] == "echo: post-recovery hello"
    )


# ===========================================================================
# Module-level lifecycle wrappers
# ===========================================================================

async def test_module_create_records_active_plugin_singleton():
    """``create()`` records the instance so module-level start_subscriber
    / stop_subscriber can find it (main.py's wiring path)."""
    plugin = PLUGIN_MOD.create()
    try:
        assert PLUGIN_MOD._active_plugin is plugin
        # Replays the main.py call shape -- with no token provider wired,
        # start_subscriber logs the graceful-degradation line and exits.
        await PLUGIN_MOD.start_subscriber()
        await PLUGIN_MOD.stop_subscriber()
        assert PLUGIN_MOD.subscriber_running() is False
    finally:
        PLUGIN_MOD._active_plugin = None
