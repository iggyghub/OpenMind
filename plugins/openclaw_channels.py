"""
OpenClaw channels MCP-client plugin -- Issue #168.

Replaces ``cerebral/bridge/openclaw.py`` (deleted as part of this PR). The
old bridge spoke a custom WS-subscribe + HTTP-POST-reply protocol that
the 2026.4.29 gateway no longer exposes. The new integration shape is
*Cerebral consumes OpenClaw's channel bridge as an MCP server* via
``openclaw mcp serve``.

Architecture
------------

The plugin spawns ``openclaw mcp serve`` as a stdio child process (the
canonical pattern per OpenClaw docs: *"the MCP client spawns
`openclaw mcp serve`"*) and forwards its nine channel-bridge tools to
Cerebral's orchestrator:

  - openclaw_conversations_list
  - openclaw_conversation_get
  - openclaw_messages_read
  - openclaw_attachments_fetch
  - openclaw_events_poll
  - openclaw_events_wait
  - openclaw_messages_send
  - openclaw_permissions_list_open
  - openclaw_permissions_respond

The plugin prefixes the upstream names with ``openclaw_`` to keep the
orchestrator's flat tool namespace collision-free (see the 2026-05-04
``_tool_index`` learning).

In parallel, a background *subscriber* coroutine drives an ``events_wait``
long-poll loop. On each inbound channel event the subscriber resolves the
message text (using ``messages_read`` when the event payload doesn't
inline it), invokes a Cerebral-supplied ``inbound_callback`` (today's
``cerebral/main.py:_bridge_process``) with ``(transcript, history)``,
and posts the LLM's reply back via ``messages_send``. A per
``session_key`` RAM history buffer is preserved (default 16 entries),
identical posture to the deleted ``ChannelBridge``.

Auth
----

``TokenProvider`` Protocol, option-B static-token posture (the
``todoist.py`` precedent -- no OAuth refresh, ``current()`` only). The
token is forwarded to the subprocess via ``--token``; the underlying
``openclaw mcp serve`` performs the WS-side ``connect.params.auth.token``
handshake on Cerebral's behalf. A bare ``gateway.auth.token`` from
``~/.openclaw/openclaw.json`` is sufficient to complete MCP initialize
and list_tools. Privileged tool calls may file an automatic
scope-upgrade request to ``openclaw devices``; the plugin surfaces the
error to the orchestrator and continues. ``SETUP.md`` documents the
``openclaw devices approve <requestId>`` step.

Graceful degradation
--------------------

If the subprocess fails to spawn or the MCP handshake fails, the plugin
logs ``[openclaw_channels] gateway unreachable -- channel bridge
disabled`` and continues as a no-op (orchestrator calls return errors;
Cerebral itself stays up). Parity with the old
``[bridge] OpenClaw unreachable`` posture.

Tests
-----

All side-effects (subprocess spawn, MCP session) are injectable via
``session_factory``. The unit suite uses a hand-rolled ``_FakeSession``
double; no real subprocess and no real gateway.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any, AsyncContextManager, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "openclaw_channels"

# ADR-0005 / Issue #168.
#
#   - secrets_read: gateway.auth.token is a sensitive credential read
#     from ~/.openclaw/openclaw.json (or OPENCLAW_GATEWAY_TOKEN env).
#     Deliberate over-declaration (the youtube.py / todoist.py posture):
#     the AST audit maps secrets_read only to keyring.* calls and would
#     not auto-require it here, but the plugin's job is to drive a
#     gateway behind an auth credential -- handing that the silent class
#     free pass is the wrong default.
#
#   - external_data_read: channel transcripts (Telegram, Discord, etc.)
#     are external data; openclaw_messages_read / openclaw_conversation_get
#     / openclaw_events_* surface that data to the orchestrator.
#
#   - external_data_write: openclaw_messages_send mutates external state
#     (delivers a message into the user's channel account). The
#     ask-class default kicks in for outbound replies.
#
#   - network_egress_local: the spawned `openclaw mcp serve` subprocess
#     opens a loopback WebSocket to ws://127.0.0.1:18789 on the
#     gateway's behalf. We do not declare network_egress_cloud -- the
#     channel backends (Telegram servers, etc.) are reached by the
#     gateway process, not by Cerebral.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_local",
})

DEFAULT_HISTORY_LIMIT = 16
DEFAULT_EVENTS_WAIT_TIMEOUT_MS = 25_000
DEFAULT_ERROR_BACKOFF_SECONDS = 5.0
ERROR_REPLY = "Sorry, something went wrong handling that message."

_ENV_URL = "OPENCLAW_GATEWAY_URL"

# Tool naming -- the upstream names are nine generic words that would
# collide the moment a second plugin wants e.g. messages_send. The
# prefix is the orchestrator-namespace insurance noted in the
# 2026-05-04 _tool_index learning.
_TOOL_PREFIX = "openclaw_"

_UPSTREAM_TOOLS = (
    "conversations_list",
    "conversation_get",
    "messages_read",
    "attachments_fetch",
    "events_poll",
    "events_wait",
    "messages_send",
    "permissions_list_open",
    "permissions_respond",
)


# ---------------------------------------------------------------------------
# Token provider seam -- the todoist.py option-B posture
# ---------------------------------------------------------------------------

class TokenProvider(Protocol):
    """Per-active-profile gateway-token handle wired from main.py.

    Carries only ``current()`` because the gateway token is a STATIC
    operator credential (read from ``~/.openclaw/openclaw.json`` or an
    env override). There is no OAuth refresh capability to describe --
    if the token is rotated, the user re-launches Cerebral. Mirrors
    ``plugins/todoist.py`` exactly.
    """

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's ``_get_openclaw_token_provider`` -- called once at
    startup after orchestrator discovery."""
    global _token_provider_factory
    _token_provider_factory = fn


# ---------------------------------------------------------------------------
# Inbound-callback seam -- main.py wires _bridge_process here
# ---------------------------------------------------------------------------

InboundCallback = Callable[[str, list[dict]], Awaitable[str]]

_inbound_callback: Optional[InboundCallback] = None


def set_inbound_callback(fn: InboundCallback) -> None:
    """Wire main.py's ``_bridge_process`` -- called once at startup. The
    subscriber loop calls this for each inbound channel event."""
    global _inbound_callback
    _inbound_callback = fn


# ---------------------------------------------------------------------------
# MCP session shape -- production wires the real mcp SDK; tests inject a fake
# ---------------------------------------------------------------------------

class _McpSession(Protocol):
    """The slice of the mcp.ClientSession surface the plugin actually uses.

    Defined as a Protocol so tests can pass a hand-rolled fake without
    pulling in the real mcp SDK. Production sessions come from the
    real ``mcp.ClientSession``.
    """

    async def initialize(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


SessionFactory = Callable[[], AsyncContextManager[_McpSession]]


def _default_session_factory(
    token: str,
    url: Optional[str] = None,
    *,
    channel_mode: str = "off",
) -> AsyncContextManager[_McpSession]:
    """Production session factory -- spawn ``openclaw mcp serve`` and
    open an MCP stdio session. Returns an async context manager.

    The token is passed as an argv parameter (``--token <token>``). It
    is visible to ``ps`` on the local machine; that is the same trust
    boundary the token already lives in (the file at
    ``~/.openclaw/openclaw.json`` is the OS-account-readable source).
    Passing through ``--token-file`` would force the plugin to write a
    temp file and clean it up -- net more attack surface for no gain.
    """
    from mcp import ClientSession, StdioServerParameters  # type: ignore
    from mcp.client.stdio import stdio_client  # type: ignore

    args = [
        "mcp", "serve",
        "--token", token,
        "--claude-channel-mode", channel_mode,
    ]
    if url:
        args.extend(["--url", url])

    params = StdioServerParameters(
        command="openclaw",
        args=args,
        env=os.environ.copy(),
    )

    @contextlib.asynccontextmanager
    async def _ctx():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    return _ctx()


# ---------------------------------------------------------------------------
# Session host -- single-task ownership of the MCP session lifecycle (#181)
# ---------------------------------------------------------------------------

class _SessionHost:
    """Run an MCP session context manager entirely inside one dedicated
    task.

    The mcp SDK's ``stdio_client`` is built on anyio task groups whose
    cancel scopes must be exited by the same task that entered them. The
    plugin previously entered the context in whichever task called
    ``_ensure_session`` and exited it from whichever task called
    ``stop_subscriber`` / ``_invalidate_session``; when the ``openclaw
    mcp serve`` subprocess died early, that cross-task teardown
    corrupted the event loop ("Attempted to exit cancel scope in a
    different task than it was entered in", "athrow(): asynchronous
    generator is already running") and cancelled unrelated in-flight
    tasks in sibling plugins (#182's symptom). Enter and exit now both
    happen inside ``_run`` -- one task, one cancel scope.
    """

    def __init__(self, cm: AsyncContextManager[_McpSession]) -> None:
        self._cm = cm
        loop = asyncio.get_running_loop()
        self._ready: asyncio.Future = loop.create_future()
        self._close = asyncio.Event()
        self._task = loop.create_task(self._run())

    async def session(self) -> _McpSession:
        """Wait for the host task to enter the context and return the
        session. Raises what ``__aenter__`` raised (wrapped in
        RuntimeError when it wasn't an ``Exception``, so callers'
        ``except Exception`` graceful-degradation paths still fire)."""
        return await asyncio.shield(self._ready)

    async def aclose(self) -> None:
        """Signal the host task to exit the context and wait for it.

        Never raises: teardown failures of a dead subprocess are logged
        at debug, matching the plugin's existing shutdown posture.
        """
        self._close.set()
        if not self._ready.done():
            # __aenter__ still in flight -- cancel it in its own task so
            # the cancel scope unwinds where it was entered.
            self._task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
        except asyncio.TimeoutError:  # pragma: no cover -- defensive
            self._task.cancel()
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass
        # Retrieve a never-awaited startup exception so asyncio doesn't
        # log "Future exception was never retrieved".
        if self._ready.done() and not self._ready.cancelled():
            self._ready.exception()

    async def _run(self) -> None:
        try:
            async with self._cm as session:
                if not self._ready.done():
                    self._ready.set_result(session)
                await self._close.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
            raise
        except BaseException as exc:
            if not self._ready.done():
                # Startup failure -- surface to session(). ExceptionGroups
                # from the SDK's TaskGroup may not be Exception subclasses;
                # normalise so _ensure_session's except Exception catches.
                if isinstance(exc, Exception):
                    self._ready.set_exception(exc)
                else:
                    self._ready.set_exception(RuntimeError(str(exc)))
                return
            # Session died after start or teardown raised (e.g. the
            # subprocess exited and the SDK emitted an ExceptionGroup).
            # All scopes unwound in this task -- nothing leaks; log only.
            logger.debug(
                "[openclaw_channels] session teardown ignored: %s", exc,
            )


# ---------------------------------------------------------------------------
# MCP CallToolResult extraction
# ---------------------------------------------------------------------------

def _extract_text(result: Any) -> str:
    """Concatenate the text fields from an MCP CallToolResult, or ``""``.

    The real SDK returns a ``CallToolResult`` with ``content: list[...]``
    where each item is a TextContent / ImageContent / etc. We only
    consume text. Tests' fake results may also be plain strings or
    dicts -- handle defensively so the plugin doesn't crash on a stub.
    """
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if not content:
        return ""
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _extract_structured(result: Any) -> Any:
    """Return the ``structuredContent`` of a CallToolResult, or ``None``.

    Servers that declare an outputSchema return the canonical JSON
    object here; otherwise the text representation is the source of
    truth. The plugin prefers structured when present.
    """
    if result is None:
        return None
    structured = getattr(result, "structuredContent", None)
    if structured is None and isinstance(result, dict):
        structured = result.get("structuredContent")
    return structured


def _parse_payload(result: Any) -> Any:
    """Best-effort JSON parse of an MCP tool result.

    Order of preference:
      1. ``result.structuredContent`` -- canonical JSON when present.
      2. ``json.loads(result.content[0].text)`` -- the conventional
         pattern when no outputSchema is declared.
      3. Raw text -- last resort, returned as a string.
    """
    structured = _extract_structured(result)
    if structured is not None:
        return structured
    text = _extract_text(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _is_error(result: Any) -> bool:
    return bool(getattr(result, "isError", False) or
                (isinstance(result, dict) and result.get("isError")))


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------

class OpenClawChannelsPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        *,
        token_provider: Optional[TokenProvider] = None,
        session_factory: Optional[Callable[..., AsyncContextManager[_McpSession]]] = None,
        inbound_callback: Optional[InboundCallback] = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        url: Optional[str] = None,
        channel_mode: str = "off",
        events_wait_timeout_ms: int = DEFAULT_EVENTS_WAIT_TIMEOUT_MS,
        error_backoff_seconds: float = DEFAULT_ERROR_BACKOFF_SECONDS,
    ) -> None:
        # Constructor injection wins over the module-level setters --
        # the same pattern todoist.py uses.
        self._provider_override = token_provider
        self._session_factory_override = session_factory
        self._inbound_override = inbound_callback
        self._history_limit = max(2, history_limit)
        self._url = url or os.environ.get(_ENV_URL) or None
        self._channel_mode = channel_mode
        self._events_wait_timeout_ms = events_wait_timeout_ms
        self._error_backoff_seconds = error_backoff_seconds

        self._sessions_history: dict[str, list[dict]] = {}
        self._session_host: Optional[_SessionHost] = None
        self._session: Optional[_McpSession] = None
        self._session_lock = asyncio.Lock()
        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._scope_warned = False  # rate-limit the scope-upgrade WARN
        self._seen_tokens: set[str] = set()

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=f"{_TOOL_PREFIX}conversations_list",
                description=(
                    "List the user's OpenClaw channel conversations "
                    "(Telegram, Discord, WhatsApp, etc.) routed through "
                    "the gateway. Optional limit, search query, channel "
                    "filter."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                        "search": {"type": "string"},
                        "channel": {"type": "string"},
                        "includeDerivedTitles": {"type": "boolean"},
                        "includeLastMessage": {"type": "boolean"},
                    },
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}conversation_get",
                description=(
                    "Get one OpenClaw channel conversation by its "
                    "session_key (e.g. 'telegram:123')."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "session_key": {"type": "string"},
                    },
                    "required": ["session_key"],
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}messages_read",
                description=(
                    "Read the most recent messages in one OpenClaw "
                    "channel conversation."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "session_key": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["session_key"],
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}attachments_fetch",
                description=(
                    "List non-text attachments on a message in an "
                    "OpenClaw conversation."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "session_key": {"type": "string"},
                        "message_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["session_key", "message_id"],
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}events_poll",
                description=(
                    "Poll queued OpenClaw channel events since a cursor "
                    "(non-blocking). Use the subscriber for live events; "
                    "this tool is for catch-up reads."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "after_cursor": {"type": "integer"},
                        "session_key": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}events_wait",
                description=(
                    "Long-poll for the next OpenClaw channel event. "
                    "Cerebral's background subscriber already consumes "
                    "events; LLM-driven use should be rare."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "after_cursor": {"type": "integer"},
                        "session_key": {"type": "string"},
                        "timeout_ms": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}messages_send",
                description=(
                    "Send a message back through an OpenClaw channel "
                    "conversation (replies inbound messages on the same "
                    "route)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "session_key": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["session_key", "text"],
                },
            ),
            Tool(
                name=f"{_TOOL_PREFIX}permissions_list_open",
                description=(
                    "List open OpenClaw exec / plugin approval requests "
                    "the gateway is asking the user to respond to."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name=f"{_TOOL_PREFIX}permissions_respond",
                description=(
                    "Allow or deny one pending OpenClaw exec / plugin "
                    "approval request. Slice-1 stub: Cerebral's own "
                    "ADR-0005 ask-class consent flow is the source of "
                    "truth; the LLM should default to 'deny' unless the "
                    "user explicitly confirmed."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["exec", "plugin"]},
                        "id": {"type": "string"},
                        "decision": {
                            "type": "string",
                            "enum": ["allow-once", "allow-always", "deny"],
                        },
                    },
                    "required": ["kind", "id", "decision"],
                },
                irreversible=True,
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if not tool_name.startswith(_TOOL_PREFIX):
            return ToolResult(
                content=f"Unknown tool: '{tool_name}'", is_error=True,
            )
        upstream = tool_name[len(_TOOL_PREFIX):]
        if upstream not in _UPSTREAM_TOOLS:
            return ToolResult(
                content=f"Unknown tool: '{tool_name}'", is_error=True,
            )

        session, err = await self._ensure_session()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            result = await session.call_tool(upstream, dict(args or {}))
        except Exception as exc:
            scrubbed = self._scrub(str(exc))
            logger.error(
                "[openclaw_channels] %s failed: %s", tool_name, scrubbed,
            )
            return ToolResult(
                content=f"openclaw {upstream} failed: {scrubbed}",
                is_error=True,
            )

        text = _extract_text(result)
        if _is_error(result):
            return ToolResult(content=self._scrub(text) or "openclaw error",
                              is_error=True)
        return ToolResult(content=self._scrub(text))

    # ------------------------------------------------------------------
    # Subscriber lifecycle
    # ------------------------------------------------------------------

    @property
    def subscriber_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start_subscriber(self) -> None:
        """Open the MCP session (if not already open) and drive the
        ``events_wait`` loop until ``stop_subscriber`` is called.

        Safe to call when the session can't be opened: logs the
        graceful-degradation line and returns. Cerebral keeps running.
        """
        if self.subscriber_running:
            logger.info(
                "[openclaw_channels] subscriber already running -- ignoring start_subscriber",
            )
            return

        callback = self._resolve_inbound_callback()
        if callback is None:
            logger.warning(
                "[openclaw_channels] inbound callback not wired -- "
                "subscriber not started",
            )
            return

        session, err = await self._ensure_session()
        if err is not None:
            # Graceful-degradation parity with the deleted bridge.
            logger.warning(
                "[openclaw_channels] gateway unreachable -- channel bridge "
                "disabled (%s)", err,
            )
            return

        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._events_wait_loop(callback))
        logger.info(
            "[openclaw_channels] Connected to OpenClaw -- subscriber loop running",
        )

    async def stop_subscriber(self) -> None:
        """Cancel the events_wait loop and tear down the MCP session.

        Idempotent. The session host exits the stdio_client (which
        terminates the subprocess) and the ClientSession in its own task.
        """
        self._stop_event.set()
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        async with self._session_lock:
            host = self._session_host
            self._session_host = None
            self._session = None
            if host is not None:
                # _SessionHost.aclose never raises; teardown happens in
                # the host's own task so a dead subprocess can't poison
                # this one's cancel scopes (#181).
                await host.aclose()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_token(self) -> tuple[Optional[str], Optional[str]]:
        """Return ``(token, error_msg)``. Exactly one is non-None.

        Token resolution lives in main.py (env + ``~/.openclaw/openclaw.json``
        lookup) and reaches the plugin via the ``TokenProvider`` Protocol --
        the same posture as ``plugins/todoist.py``. The plugin itself does
        no env / config-file reads.
        """
        provider = self._provider_override
        if provider is None and _token_provider_factory is not None:
            try:
                provider = _token_provider_factory()
            except Exception as exc:  # pragma: no cover -- defensive
                return None, f"token provider raised: {exc}"
        if provider is None:
            return None, "token provider not wired"
        try:
            token = provider.current()
        except Exception as exc:  # pragma: no cover -- defensive
            return None, f"token provider raised: {exc}"
        if not token:
            return None, "no gateway token configured"
        self._seen_tokens.add(token)
        return token, None

    def _resolve_inbound_callback(self) -> Optional[InboundCallback]:
        if self._inbound_override is not None:
            return self._inbound_override
        return _inbound_callback

    async def _ensure_session(self) -> tuple[Optional[_McpSession], Optional[str]]:
        """Lazy session bring-up. Returns ``(session, None)`` on success
        or ``(None, error_msg)`` on graceful-degradation."""
        if self._session is not None:
            return self._session, None

        async with self._session_lock:
            if self._session is not None:
                return self._session, None

            token, err = self._resolve_token()
            if err is not None:
                return None, err

            factory = self._session_factory_override
            try:
                if factory is None:
                    cm = _default_session_factory(
                        token=token or "",
                        url=self._url,
                        channel_mode=self._channel_mode,
                    )
                else:
                    cm = factory()
            except Exception as exc:
                scrubbed = self._scrub(str(exc))
                return None, f"session start failed: {scrubbed}"

            host = _SessionHost(cm)
            try:
                session = await host.session()
            except asyncio.CancelledError:
                # Caller cancelled mid bring-up (stop_subscriber during
                # the events_wait loop's reconnect). Close the host so
                # its task doesn't leak with the context half-entered.
                await host.aclose()
                raise
            except Exception as exc:
                await host.aclose()
                scrubbed = self._scrub(str(exc))
                return None, f"session start failed: {scrubbed}"

            self._session_host = host
            self._session = session
            return session, None

    async def _events_wait_loop(self, callback: InboundCallback) -> None:
        """Drive ``events_wait`` until stopped, forwarding events to the
        Cerebral-supplied ``callback`` and replying via ``messages_send``."""
        after_cursor = 0
        try:
            while not self._stop_event.is_set():
                session, err = await self._ensure_session()
                if err is not None or session is None:
                    logger.warning(
                        "[openclaw_channels] session unavailable in loop: %s "
                        "-- backing off", err,
                    )
                    await self._sleep_or_stop(self._error_backoff_seconds)
                    continue

                try:
                    result = await session.call_tool(
                        "events_wait",
                        {
                            "after_cursor": after_cursor,
                            "timeout_ms": self._events_wait_timeout_ms,
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Per the 2026-05-27 "scope-upgrade rejection
                    # surfaces as a closed WS" learning: the 2026.4.29
                    # gateway closes the WebSocket when a privileged
                    # call (events_wait included) hits an unapproved
                    # scope, instead of returning a structured
                    # ``isError=True`` result. The mcp SDK then raises
                    # ``McpError("Connection closed")`` (CONNECTION_CLOSED
                    # code) on the next read. The actionable WARN in the
                    # ``_is_error`` branch below is therefore unreachable
                    # on this path -- handle the actionable hint here
                    # for the FIRST raise after subscriber start. The
                    # ``_scope_warned`` flag rate-limits subsequent
                    # raises so the operator log doesn't flood.
                    scrubbed = self._scrub(str(exc))
                    if not self._scope_warned:
                        logger.warning(
                            "[openclaw_channels] events_wait raised "
                            "(likely scope upgrade pending) -- approve "
                            "via `openclaw devices approve --latest` "
                            "(use `openclaw devices list-pending` to "
                            "surface the requestId; see SETUP.md). "
                            "Detail: %s",
                            scrubbed or "<no exception text>",
                        )
                        self._scope_warned = True
                    else:
                        logger.warning(
                            "[openclaw_channels] events_wait raised: %s "
                            "-- backing off",
                            scrubbed or "<no exception text>",
                        )
                    # Session-killing raises invalidate the cached MCP
                    # session. Without this, _ensure_session keeps
                    # returning the dead reference and every subsequent
                    # call_tool raises with empty exception text; even
                    # after the operator approves the scope out-of-band
                    # the bridge never recovers without a Cerebral
                    # restart. Reset and let the next iteration spawn a
                    # fresh ``openclaw mcp serve`` child.
                    await self._invalidate_session()
                    await self._sleep_or_stop(self._error_backoff_seconds)
                    continue

                if _is_error(result):
                    text = _extract_text(result) or "<no body>"
                    if "scope upgrade" in text.lower() and not self._scope_warned:
                        logger.warning(
                            "[openclaw_channels] gateway requires scope "
                            "upgrade -- approve via "
                            "`openclaw devices approve <requestId>` "
                            "(see SETUP.md). Detail: %s",
                            self._scrub(text),
                        )
                        self._scope_warned = True
                    else:
                        logger.warning(
                            "[openclaw_channels] events_wait error: %s",
                            self._scrub(text),
                        )
                    await self._sleep_or_stop(self._error_backoff_seconds)
                    continue

                payload = _parse_payload(result)
                events = self._normalize_events(payload)
                if not events:
                    # Timeout/empty -- normal for long-poll, just keep going.
                    continue

                for evt in events:
                    cursor = evt.get("cursor")
                    if isinstance(cursor, int) and cursor > after_cursor:
                        after_cursor = cursor
                    try:
                        await self._process_event(evt, callback, session)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # pragma: no cover -- defensive
                        logger.exception(
                            "[openclaw_channels] processing event raised: %s",
                            self._scrub(str(exc)),
                        )
        except asyncio.CancelledError:
            raise
        finally:
            logger.info("[openclaw_channels] subscriber loop stopped")

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep, but wake immediately if stop_subscriber() is called."""
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _invalidate_session(self) -> None:
        """Tear down the cached MCP session so the next ``_ensure_session``
        call respawns a fresh ``openclaw mcp serve`` child.

        Used by the events_wait loop after a session-killing raise: the
        2026.4.29 gateway closes the WebSocket on scope-upgrade
        rejection (and other transport failures), but the cached
        ``ClientSession`` reference stays around. Without this, the
        operator approving the scope out-of-band still requires a full
        Cerebral restart -- the loop never reconnects.

        Idempotent; swallows shutdown exceptions per the same posture
        as ``stop_subscriber`` (the upstream mcp SDK can emit an
        ExceptionGroup on stdio_client teardown when openclaw mcp
        serve writes a non-JSON line to stdout during close).
        """
        async with self._session_lock:
            host = self._session_host
            self._session_host = None
            self._session = None
            if host is None:
                return
            await host.aclose()

    @staticmethod
    def _normalize_events(payload: Any) -> list[dict]:
        """The events_wait payload may arrive in any of a handful of
        shapes -- be permissive."""
        if payload is None:
            return []
        if isinstance(payload, dict):
            events = payload.get("events")
            if isinstance(events, list):
                return [e for e in events if isinstance(e, dict)]
            # Single-event shape: the dict itself is the event.
            if any(k in payload for k in ("session_key", "type", "message")):
                return [payload]
            return []
        if isinstance(payload, list):
            return [e for e in payload if isinstance(e, dict)]
        return []

    async def _process_event(
        self,
        event: dict,
        callback: InboundCallback,
        session: _McpSession,
    ) -> None:
        session_key = event.get("session_key")
        if not isinstance(session_key, str) or not session_key:
            return

        text = self._extract_event_text(event)
        if not text:
            # Event didn't carry text inline -- fetch the latest message.
            text = await self._fetch_latest_message_text(session, session_key)

        if not text:
            return

        history = list(self._sessions_history.get(session_key, []))

        try:
            reply = await callback(text, history)
        except Exception as exc:
            logger.exception(
                "[openclaw_channels] inbound callback raised for %s: %s",
                session_key, self._scrub(str(exc)),
            )
            reply = ERROR_REPLY
        else:
            self._append_history(session_key, "user", text)
            self._append_history(session_key, "assistant", reply)

        if not reply:
            return

        try:
            send_result = await session.call_tool(
                "messages_send",
                {"session_key": session_key, "text": reply},
            )
        except Exception as exc:
            logger.warning(
                "[openclaw_channels] messages_send to %s failed: %s",
                session_key, self._scrub(str(exc)),
            )
            return
        if _is_error(send_result):
            logger.warning(
                "[openclaw_channels] messages_send to %s returned error: %s",
                session_key, self._scrub(_extract_text(send_result)),
            )

    @staticmethod
    def _extract_event_text(event: dict) -> str:
        """Pull the inbound message text out of an events_wait payload.

        Tolerant of the variety of shapes the gateway might emit -- the
        exact payload schema is undocumented at the time of writing and
        the slice-1 unit suite couldn't probe it against the live
        gateway (scope upgrade pending). Look for the obvious fields,
        return ``""`` if none match (in which case the caller falls
        back to messages_read).
        """
        for key in ("text", "body"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value
        message = event.get("message")
        if isinstance(message, dict):
            for key in ("text", "body"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return ""

    async def _fetch_latest_message_text(
        self, session: _McpSession, session_key: str,
    ) -> str:
        try:
            result = await session.call_tool(
                "messages_read", {"session_key": session_key, "limit": 1},
            )
        except Exception as exc:
            logger.warning(
                "[openclaw_channels] messages_read fallback for %s failed: %s",
                session_key, self._scrub(str(exc)),
            )
            return ""
        if _is_error(result):
            return ""
        payload = _parse_payload(result)
        messages: list = []
        if isinstance(payload, dict):
            raw = payload.get("messages")
            if isinstance(raw, list):
                messages = raw
        elif isinstance(payload, list):
            messages = payload
        if not messages:
            return ""
        last = messages[-1]
        if isinstance(last, dict):
            for key in ("text", "body"):
                value = last.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return ""

    # ------------------------------------------------------------------
    # History buffer -- lift from the deleted ChannelBridge
    # ------------------------------------------------------------------

    def _append_history(self, session_key: str, role: str, text: str) -> None:
        buf = self._sessions_history.setdefault(session_key, [])
        buf.append({"role": role, "text": text})
        if len(buf) > self._history_limit:
            del buf[: len(buf) - self._history_limit]

    def get_history(self, session_key: str) -> list[dict]:
        return list(self._sessions_history.get(session_key, []))

    def reset_session(self, session_key: str) -> None:
        self._sessions_history.pop(session_key, None)

    def _scrub(self, text: str) -> str:
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text


# ---------------------------------------------------------------------------
# Module-level lifecycle wrappers -- main.py drives these
# ---------------------------------------------------------------------------

_active_plugin: Optional[OpenClawChannelsPlugin] = None


def create() -> OpenClawChannelsPlugin:
    """Orchestrator entry point. Records the singleton so the
    module-level lifecycle wrappers can find it."""
    global _active_plugin
    _active_plugin = OpenClawChannelsPlugin()
    return _active_plugin


async def start_subscriber() -> None:
    """Module-level start_subscriber the way main.py drives lifecycle
    on the orchestrator-loaded module instance (Issue #153 -- same
    importlib-spec concern todoist.py's set_token_provider has)."""
    if _active_plugin is None:
        logger.warning(
            "[openclaw_channels] start_subscriber called with no active "
            "plugin instance -- did discovery succeed?",
        )
        return
    await _active_plugin.start_subscriber()


async def stop_subscriber() -> None:
    if _active_plugin is None:
        return
    await _active_plugin.stop_subscriber()


def subscriber_running() -> bool:
    if _active_plugin is None:
        return False
    return _active_plugin.subscriber_running
