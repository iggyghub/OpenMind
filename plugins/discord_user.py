"""
Discord user-account plugin -- Issue #175 (slice 1), ADR-0006.

Lets Felix read incoming DMs from real humans on the *user's personal
Discord account* and surface them as drafts for manual approval. This
is the **self-bot** path: the credential is the user's actual
user-account token, NOT a registered bot token. This is wholly
independent of ``plugins/openclaw_channels.py`` (#168 / PR #171) and
does NOT consume OpenClaw's bot-channel surface. See ADR-0006 for the
ToS-violation acknowledgement.

Slice 1 (PR #176) shipped:
  - Tool surface (LLM-callable):
      * ``discord_list_conversations`` -- recent DM threads, summary view.
      * ``discord_get_messages``       -- channel/DM history.
      * ``discord_send_message``       -- send to a channel/DM, with a
        required ``confirm: true`` gate. Without ``confirm``, returns
        the would-be message for the LLM/user to review.
      * ``discord_set_presence``       -- desired online/idle/dnd/
        invisible state. Slice-1 stub: records the desired state and
        applies it on the next subscriber connect.
  - Inbound: subscriber loop driven by a ``DiscordClient`` (default
    factory lazy-imports ``discord.py-self``). On each inbound DM the
    plugin invokes the wired ``draft_callback`` (today's
    ``cerebral/main.py:_surface_discord_draft``) with a draft event
    dict.

Slice 2 (Issue #177) adds the slice-2 internal sender surface
(``trigger_typing`` + ``send_dm``) the auto-reply controller in
``cerebral/discord_auto_reply.py`` drives when an inbound DM lands
from an allowlisted sender. The controller lives Cerebral-side -- the
plugin stays focused on Discord I/O.

Slice 3 (Issue #178) rounds out the outbound surface and adds dynamic
presence automation:

  - ``discord_react``  -- add or remove an emoji reaction on a message
    (DM channel). Confirm-gated, irreversible=True.
  - ``discord_edit``   -- edit a message owned by the authenticated
    user. Confirm-gated, irreversible=True. Surfaces Discord's
    ownership-violation error rather than checking client-side.
  - ``discord_delete`` -- delete a message owned by the authenticated
    user. Confirm-gated, irreversible=True. Same ownership posture.
  - Auto-presence is driven by a Cerebral-side controller
    (``cerebral/discord_presence.py``); the plugin still owns the
    actual ``change_presence`` wire call via the slice-1
    ``DiscordClient`` Protocol. The slice-1 ``discord_set_presence``
    MCP tool remains the manual-override path and continues to win
    until the next auto-trigger.

Architecture
------------

Two side-effect surfaces, both injectable:

  - ``fetch_fn`` (todoist.py precedent) -- raw HTTPS to
    ``discord.com/api/v10``. Drives the four REST tools above. Defaults
    to aiohttp -> httpx -> hard error.
  - ``client_factory`` -- builds a ``DiscordClient`` for the inbound
    gateway subscriber. Default factory lazy-imports ``discord.py-self``
    inside the function body so a missing install degrades gracefully
    to "Discord user plugin not available" rather than crashing
    Cerebral at import time.

Auth
----

``TokenProvider`` Protocol, option-B static-token posture (the
``todoist.py`` precedent -- no OAuth refresh, ``current()`` only). The
token reaches the plugin via a module-level ``set_token_provider``
setter wired from ``cerebral/main.py``. The plugin itself does no env
/ keyring reads.

Token is validated against ``GET /users/@me`` on subscriber start;
failure -> subscriber disabled with an actionable error, parity with
the openclaw_channels graceful-degradation posture.

Scrub
-----

Every bearer token ever held this call is recorded in
``self._seen_tokens`` and stripped from any log line / ToolResult
content before it leaves the process. Discord's user-account token is
*never* logged in clear.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import (
    Any, AsyncIterator, Awaitable, Callable, Optional, Protocol,
)

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "discord_user"

# ADR-0006 / Issue #175.
#
#   - secrets_read: the user-account token is a sensitive personal
#     credential (loss = permanent Discord account ban). Deliberate
#     over-declaration in the youtube.py / todoist.py / openclaw_channels.py
#     posture -- the AST audit would not auto-require it (the plugin
#     never calls keyring.* directly), but handing a self-bot credential
#     the silent-class free pass is the wrong default.
#
#   - external_data_read: discord_list_conversations + discord_get_messages
#     read the user's DM transcripts (external data).
#
#   - external_data_write: discord_send_message mutates external state
#     (delivers a real DM into the recipient's client). Ask-class
#     default applies; the per-call ``confirm`` gate is in addition,
#     not a replacement.
#
#   - network_egress_cloud: the plugin reaches discord.com and
#     gateway.discord.gg directly -- this is cloud egress, not the
#     loopback-only network_egress_local that openclaw_channels uses.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://discord.com/api/v10"
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100
_MIN_LIMIT = 1

_VALID_PRESENCE = ("online", "idle", "dnd", "invisible")

_FACTORY_NOT_WIRED_MSG = (
    "Discord user plugin is not available -- token provider not wired"
)
_NO_TOKEN_MSG = "no Discord user-account token configured"
_CONFIRM_REQUIRED_MSG = (
    "discord_send_message requires confirm=true to actually send. "
    "Without it, returning the would-be message for review."
)
_REACT_CONFIRM_REQUIRED_MSG = (
    "discord_react requires confirm=true to actually apply the "
    "reaction. Without it, returning the would-be reaction for review."
)
_EDIT_CONFIRM_REQUIRED_MSG = (
    "discord_edit requires confirm=true to actually edit the message. "
    "Without it, returning the would-be edit for review."
)
_DELETE_CONFIRM_REQUIRED_MSG = (
    "discord_delete requires confirm=true to actually delete the "
    "message. Without it, returning the would-be deletion for review."
)
_VALID_REACTION_ACTIONS = ("add", "remove")


# ---------------------------------------------------------------------------
# Token provider seam -- the todoist.py option-B posture
# ---------------------------------------------------------------------------

class TokenProvider(Protocol):
    """Per-active-profile Discord user-account token handle wired from
    main.py.

    Carries ONLY ``current()`` because the token is a STATIC user-
    extracted value -- there is no OAuth refresh capability to describe.
    Mirrors ``plugins/todoist.py`` exactly.
    """

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's ``_get_discord_user_token_provider`` -- called once
    at startup after orchestrator discovery."""
    global _token_provider_factory
    _token_provider_factory = fn


# ---------------------------------------------------------------------------
# Draft-callback seam -- main.py wires _surface_discord_draft here
# ---------------------------------------------------------------------------

DraftCallback = Callable[[dict], Awaitable[None]]

_draft_callback: Optional[DraftCallback] = None


def set_draft_callback(fn: DraftCallback) -> None:
    """Wire main.py's draft-surfacing function -- called once at startup.

    Slice-1 contract: the subscriber loop calls this for each inbound
    DM. The callback does NOT return a reply. Auto-reply is slice 2.
    """
    global _draft_callback
    _draft_callback = fn


# ---------------------------------------------------------------------------
# Slice-3 presence-controller seams -- main.py wires these
# ---------------------------------------------------------------------------

# Fired after a confirmed outbound tool call clears its confirm gate
# (slice-3 acceptance: auto-online on the next LLM-driven Discord
# action). Tick is fire-and-forget; exceptions are swallowed so a
# failing controller never breaks the tool path.
ActivityCallback = Callable[[], Awaitable[None]]

_activity_callback: Optional[ActivityCallback] = None


def set_activity_callback(fn: Optional[ActivityCallback]) -> None:
    """Wire main.py's presence-activity tick. The plugin calls it after
    every confirmed outbound tool call (send / react / edit / delete).
    Passing ``None`` clears the seam (used by tests for isolation)."""
    global _activity_callback
    _activity_callback = fn


# Manual-override delegate -- when wired, ``discord_set_presence``
# routes through this instead of applying directly. Returns True iff
# the callback handled the change.
ManualPresenceCallback = Callable[[str], Awaitable[bool]]

_manual_presence_callback: Optional[ManualPresenceCallback] = None


def set_manual_presence_callback(
    fn: Optional[ManualPresenceCallback],
) -> None:
    """Wire main.py's manual-override delegate. Without it,
    ``discord_set_presence`` keeps its slice-1 direct-apply
    behaviour."""
    global _manual_presence_callback
    _manual_presence_callback = fn


# ---------------------------------------------------------------------------
# HTTP transport -- the todoist.py precedent
# ---------------------------------------------------------------------------

FetchFn = Callable[..., Awaitable[Any]]


class DiscordAPIError(RuntimeError):
    """Any transport/HTTP failure of a Discord REST call. ``status`` is
    the HTTP status code when one is available, else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


async def _default_fetch(
    method: str, url: str, *,
    headers: dict | None = None,
    params: dict | None = None,
    json: dict | None = None,
) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed
    JSON or ``None`` for 204. Lifts the todoist.py shape verbatim
    (learning #12: lazy import keeps module-level import stdlib-only).
    """
    try:
        import aiohttp  # type: ignore
    except ImportError:
        aiohttp = None  # type: ignore
    if aiohttp is not None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url, headers=headers, params=params, json=json,
                ) as resp:
                    resp.raise_for_status()
                    if resp.status == 204:
                        return None
                    return await resp.json()
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise DiscordAPIError(str(exc), status=exc.status) from exc
        except DiscordAPIError:
            raise
        except Exception as exc:
            raise DiscordAPIError(str(exc), status=None) from exc

    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None  # type: ignore
    if httpx is not None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json,
                )
                resp.raise_for_status()
                if resp.status_code == 204:
                    return None
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise DiscordAPIError(
                str(exc), status=exc.response.status_code,
            ) from exc
        except DiscordAPIError:
            raise
        except Exception as exc:
            raise DiscordAPIError(str(exc), status=None) from exc

    raise DiscordAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


# ---------------------------------------------------------------------------
# Discord gateway client seam -- production wires discord.py-self; tests inject
# ---------------------------------------------------------------------------

class DiscordClient(Protocol):
    """The slice of ``discord.py-self.Client`` the subscriber uses.

    Defined as a Protocol so tests can pass a hand-rolled fake without
    installing ``discord.py-self``. Production clients come from
    ``_default_client_factory``.

    Contract:

      - ``set_message_handler(cb)``: register an async callback fired
        for each inbound message. The callback is invoked with one
        argument -- a *message-like* object exposing
        ``id`` / ``content`` / ``author`` / ``channel`` attributes that
        ``_extract_event`` knows how to read.
      - ``start(token)``: open the gateway connection. Blocks until
        ``close()`` is called. The plugin awaits this as a background
        task; cancellation closes the connection.
      - ``close()``: graceful shutdown.
      - ``change_presence(status)``: apply the desired status.
    """

    def set_message_handler(
        self, cb: Callable[[Any], Awaitable[None]],
    ) -> None: ...
    async def start(self, token: str) -> None: ...
    async def close(self) -> None: ...
    async def change_presence(self, *, status: str) -> None: ...


ClientFactory = Callable[[], DiscordClient]


def _default_client_factory() -> DiscordClient:
    """Production client factory -- lazy-import ``discord.py-self`` and
    wrap its ``Client`` in the Protocol shape.

    Lazy-imported inside the function body so the plugin remains
    importable (and the orchestrator can still discover it for the
    purpose of reporting a "Discord user plugin not available --
    install discord.py-self") when the dependency isn't installed.
    """
    try:
        import discord  # type: ignore  # discord.py-self installs as ``discord``
    except ImportError as exc:
        raise RuntimeError(
            "discord.py-self is not installed -- install it with "
            "`pip install discord.py-self` to enable the discord_user "
            "plugin",
        ) from exc

    class _RealClient:
        def __init__(self) -> None:
            self._client = discord.Client()
            self._handler: Optional[Callable[[Any], Awaitable[None]]] = None

            @self._client.event
            async def on_message(message: Any) -> None:  # type: ignore[no-redef]
                if self._handler is not None:
                    await self._handler(message)

        def set_message_handler(
            self, cb: Callable[[Any], Awaitable[None]],
        ) -> None:
            self._handler = cb

        async def start(self, token: str) -> None:
            await self._client.start(token)

        async def close(self) -> None:
            await self._client.close()

        async def change_presence(self, *, status: str) -> None:
            await self._client.change_presence(
                status=discord.Status[status],
            )

    return _RealClient()


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------

class DiscordUserPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        *,
        token_provider: Optional[TokenProvider] = None,
        fetch_fn: FetchFn | None = None,
        client_factory: Optional[ClientFactory] = None,
        draft_callback: Optional[DraftCallback] = None,
    ) -> None:
        # Constructor injection wins over the module-level setters --
        # the todoist.py / openclaw_channels.py pattern.
        self._provider_override = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._client_factory_override = client_factory
        self._draft_override = draft_callback

        self._seen_tokens: set[str] = set()
        self._client: Optional[DiscordClient] = None
        self._client_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        # Slice-1 set_presence stub: record the desired state; the
        # client applies it on the next connect.
        self._desired_presence: Optional[str] = None

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="discord_list_conversations",
                description=(
                    "List the user's recent Discord DM threads. Returns "
                    "id, recipient name(s), and last-message preview. "
                    "DM-only for v1; server channels are not surfaced."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Maximum threads to return (default "
                                f"{_DEFAULT_LIMIT}, clamped to "
                                f"[{_MIN_LIMIT}, {_MAX_LIMIT}])."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="discord_get_messages",
                description=(
                    "Read recent messages in a Discord DM or channel. "
                    "Returns id, author name, content, and timestamp "
                    "for each message, newest-first."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": (
                                "Discord channel/DM id (from "
                                "discord_list_conversations)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Maximum messages to return (default "
                                f"{_DEFAULT_LIMIT}, clamped to "
                                f"[{_MIN_LIMIT}, {_MAX_LIMIT}])."
                            ),
                        },
                        "before": {
                            "type": "string",
                            "description": (
                                "Cursor message id; return messages "
                                "older than this one."
                            ),
                        },
                    },
                    "required": ["channel_id"],
                },
            ),
            Tool(
                name="discord_send_message",
                description=(
                    "Send a message to a Discord DM or channel as the "
                    "user. REQUIRES confirm=true to actually send -- "
                    "without it, returns the would-be message for "
                    "review."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": (
                                "Discord channel/DM id to send to."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "Message text.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to actually send. False "
                                "(default) returns the would-be "
                                "message for review."
                            ),
                        },
                    },
                    "required": ["channel_id", "content"],
                },
                irreversible=True,
            ),
            Tool(
                name="discord_react",
                description=(
                    "Add or remove an emoji reaction on a Discord DM "
                    "message. REQUIRES confirm=true to actually apply "
                    "the reaction -- without it, returns the would-be "
                    "reaction for review. Unicode emoji or custom "
                    "``name:id`` accepted. Discord enforces that "
                    "remove acts only on reactions previously added "
                    "by the authenticated user; surface the error "
                    "rather than catch."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": (
                                "Discord channel/DM id the target "
                                "message lives in."
                            ),
                        },
                        "message_id": {
                            "type": "string",
                            "description": "Target message id.",
                        },
                        "emoji": {
                            "type": "string",
                            "description": (
                                "Unicode emoji (e.g. thumbs-up "
                                "\"\\U0001F44D\") or custom emoji "
                                "as ``name:id``."
                            ),
                        },
                        "action": {
                            "type": "string",
                            "enum": list(_VALID_REACTION_ACTIONS),
                            "description": (
                                "``add`` (default) or ``remove``."
                            ),
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to actually apply. "
                                "False (default) returns the would-be "
                                "reaction for review."
                            ),
                        },
                    },
                    "required": [
                        "channel_id", "message_id", "emoji",
                    ],
                },
                irreversible=True,
            ),
            Tool(
                name="discord_edit",
                description=(
                    "Edit a Discord message owned by the authenticated "
                    "user. REQUIRES confirm=true to actually apply -- "
                    "without it, returns the would-be edit for review. "
                    "Discord rejects edits to messages not owned by "
                    "the authenticated user; the error is surfaced "
                    "rather than caught."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": (
                                "Discord channel/DM id."
                            ),
                        },
                        "message_id": {
                            "type": "string",
                            "description": "Target message id.",
                        },
                        "new_content": {
                            "type": "string",
                            "description": "Replacement message text.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to actually edit. "
                                "False (default) returns the would-be "
                                "edit for review."
                            ),
                        },
                    },
                    "required": [
                        "channel_id", "message_id", "new_content",
                    ],
                },
                irreversible=True,
            ),
            Tool(
                name="discord_delete",
                description=(
                    "Delete a Discord message owned by the "
                    "authenticated user. REQUIRES confirm=true to "
                    "actually delete -- without it, returns the "
                    "would-be deletion for review. Discord rejects "
                    "deletes of messages not owned by the "
                    "authenticated user; the error is surfaced rather "
                    "than caught."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": (
                                "Discord channel/DM id."
                            ),
                        },
                        "message_id": {
                            "type": "string",
                            "description": "Target message id.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to actually delete. "
                                "False (default) returns the would-be "
                                "deletion for review."
                            ),
                        },
                    },
                    "required": ["channel_id", "message_id"],
                },
                irreversible=True,
            ),
            Tool(
                name="discord_set_presence",
                description=(
                    "Set the user's Discord presence status. Slice-1: "
                    "records the desired status; applied on the next "
                    "subscriber connect."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": list(_VALID_PRESENCE),
                            "description": (
                                "Desired presence: online, idle, dnd, "
                                "or invisible."
                            ),
                        },
                    },
                    "required": ["status"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        if tool_name == "discord_list_conversations":
            return await self._list_conversations(args)
        if tool_name == "discord_get_messages":
            return await self._get_messages(args)
        if tool_name == "discord_send_message":
            return await self._send_message(args)
        if tool_name == "discord_react":
            return await self._react(args)
        if tool_name == "discord_edit":
            return await self._edit(args)
        if tool_name == "discord_delete":
            return await self._delete(args)
        if tool_name == "discord_set_presence":
            return await self._set_presence(args)
        return ToolResult(
            content=f"Unknown tool: '{tool_name}'", is_error=True,
        )

    # ------------------------------------------------------------------
    # Tool implementations -- REST via fetch_fn
    # ------------------------------------------------------------------

    async def _list_conversations(self, args: dict) -> ToolResult:
        limit = self._clamp_limit(args.get("limit"))
        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        try:
            raw = await self._request(
                "GET", "users/@me/channels", token,
            )
        except Exception as exc:
            return self._fail("discord_list_conversations", exc)

        if not isinstance(raw, list):
            return ToolResult(
                content="unexpected Discord list response", is_error=True,
            )

        # Filter to DM-like channels (type 1=DM, 3=GroupDM). v1 surfaces
        # both; servers (type 0/text) are out of scope.
        threads = []
        for ch in raw[:limit]:
            if not isinstance(ch, dict):
                continue
            ctype = ch.get("type")
            if ctype not in (1, 3):
                continue
            threads.append(_shape_conversation(ch))
        return ToolResult(content=json.dumps({"conversations": threads}))

    async def _get_messages(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        if not isinstance(channel_id, str) or not channel_id:
            return ToolResult(
                content="missing required arg(s) for discord_get_messages: "
                        "channel_id",
                is_error=True,
            )
        limit = self._clamp_limit(args.get("limit"))
        params: dict = {"limit": limit}
        before = args.get("before")
        if isinstance(before, str) and before:
            params["before"] = before

        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        try:
            raw = await self._request(
                "GET", f"channels/{channel_id}/messages", token,
                params=params,
            )
        except Exception as exc:
            return self._fail("discord_get_messages", exc)

        if not isinstance(raw, list):
            return ToolResult(
                content="unexpected Discord messages response",
                is_error=True,
            )
        messages = [_shape_message(m) for m in raw if isinstance(m, dict)]
        return ToolResult(content=json.dumps({"messages": messages}))

    async def _send_message(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        content = args.get("content")
        if not isinstance(channel_id, str) or not channel_id:
            return ToolResult(
                content="missing required arg(s) for discord_send_message: "
                        "channel_id",
                is_error=True,
            )
        if not isinstance(content, str) or not content:
            return ToolResult(
                content="missing required arg(s) for discord_send_message: "
                        "content",
                is_error=True,
            )
        if not args.get("confirm"):
            # Confirm gate: return the would-be message without sending.
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "preview": content,
                "note": _CONFIRM_REQUIRED_MSG,
            }))

        await self._fire_activity()
        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        try:
            resp = await self._request(
                "POST", f"channels/{channel_id}/messages", token,
                json={"content": content},
            )
        except Exception as exc:
            return self._fail("discord_send_message", exc)

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Discord send response", is_error=True,
            )
        return ToolResult(content=json.dumps({
            "confirmed": True,
            "message": _shape_message(resp),
        }))

    async def _react(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        message_id = args.get("message_id")
        emoji = args.get("emoji")
        action = args.get("action") or "add"
        if not isinstance(channel_id, str) or not channel_id:
            return ToolResult(
                content="missing required arg(s) for discord_react: "
                        "channel_id",
                is_error=True,
            )
        if not isinstance(message_id, str) or not message_id:
            return ToolResult(
                content="missing required arg(s) for discord_react: "
                        "message_id",
                is_error=True,
            )
        if not isinstance(emoji, str) or not emoji:
            return ToolResult(
                content="missing required arg(s) for discord_react: "
                        "emoji",
                is_error=True,
            )
        if action not in _VALID_REACTION_ACTIONS:
            return ToolResult(
                content=(
                    f"invalid action: must be one of "
                    f"{_VALID_REACTION_ACTIONS}"
                ),
                is_error=True,
            )
        if not args.get("confirm"):
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "message_id": message_id,
                "emoji": emoji,
                "action": action,
                "note": _REACT_CONFIRM_REQUIRED_MSG,
            }))

        await self._fire_activity()
        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        # Discord PUT/DELETE on /channels/{c}/messages/{m}/reactions/{e}/@me
        # operate on the authenticated user's own reaction. ``@me`` is
        # the only valid target for both add and remove (removing another
        # user's reaction is a manage_messages bot operation, not a self-
        # bot one). Both endpoints return 204; transport returns None.
        method = "PUT" if action == "add" else "DELETE"
        encoded = _encode_reaction(emoji)
        try:
            await self._request(
                method,
                f"channels/{channel_id}/messages/{message_id}/"
                f"reactions/{encoded}/@me",
                token,
            )
        except Exception as exc:
            return self._fail("discord_react", exc)

        return ToolResult(content=json.dumps({
            "confirmed": True,
            "channel_id": channel_id,
            "message_id": message_id,
            "emoji": emoji,
            "action": action,
        }))

    async def _edit(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        message_id = args.get("message_id")
        new_content = args.get("new_content")
        if not isinstance(channel_id, str) or not channel_id:
            return ToolResult(
                content="missing required arg(s) for discord_edit: "
                        "channel_id",
                is_error=True,
            )
        if not isinstance(message_id, str) or not message_id:
            return ToolResult(
                content="missing required arg(s) for discord_edit: "
                        "message_id",
                is_error=True,
            )
        if not isinstance(new_content, str) or not new_content:
            return ToolResult(
                content="missing required arg(s) for discord_edit: "
                        "new_content",
                is_error=True,
            )
        if not args.get("confirm"):
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "message_id": message_id,
                "preview": new_content,
                "note": _EDIT_CONFIRM_REQUIRED_MSG,
            }))

        await self._fire_activity()
        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        # Discord PATCH /channels/{c}/messages/{m} -- Discord enforces
        # ownership server-side (returns 403/404 with code 50005 if the
        # target wasn't authored by the authenticated user). We surface
        # the error rather than check client-side, per ADR-0006 (slice 3
        # acceptance criterion).
        try:
            resp = await self._request(
                "PATCH",
                f"channels/{channel_id}/messages/{message_id}",
                token,
                json={"content": new_content},
            )
        except Exception as exc:
            return self._fail("discord_edit", exc)

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Discord edit response", is_error=True,
            )
        return ToolResult(content=json.dumps({
            "confirmed": True,
            "message": _shape_message(resp),
        }))

    async def _delete(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        message_id = args.get("message_id")
        if not isinstance(channel_id, str) or not channel_id:
            return ToolResult(
                content="missing required arg(s) for discord_delete: "
                        "channel_id",
                is_error=True,
            )
        if not isinstance(message_id, str) or not message_id:
            return ToolResult(
                content="missing required arg(s) for discord_delete: "
                        "message_id",
                is_error=True,
            )
        if not args.get("confirm"):
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "message_id": message_id,
                "note": _DELETE_CONFIRM_REQUIRED_MSG,
            }))

        await self._fire_activity()
        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        # Discord DELETE /channels/{c}/messages/{m} -- 403/404 returned
        # by Discord when the message isn't owned by the authenticated
        # user. Surface, don't catch.
        try:
            await self._request(
                "DELETE",
                f"channels/{channel_id}/messages/{message_id}",
                token,
            )
        except Exception as exc:
            return self._fail("discord_delete", exc)

        return ToolResult(content=json.dumps({
            "confirmed": True,
            "channel_id": channel_id,
            "message_id": message_id,
        }))

    async def _set_presence(self, args: dict) -> ToolResult:
        status = args.get("status")
        if status not in _VALID_PRESENCE:
            return ToolResult(
                content=(
                    f"invalid status: must be one of {_VALID_PRESENCE}"
                ),
                is_error=True,
            )
        # Slice-3: when the presence controller is wired, the LLM-callable
        # discord_set_presence flows through controller.apply_manual_override
        # so the controller can track the override and skip its next auto-
        # flip. The controller calls plugin.apply_presence under the hood
        # for the actual wire-level change. When no controller is wired
        # (slice-1 behaviour), we fall through to the direct-apply path.
        cb = _manual_presence_callback
        if cb is not None:
            self._desired_presence = status
            try:
                handled = await cb(status)
            except Exception as exc:
                logger.warning(
                    "[discord_user] manual_presence_callback raised: %s",
                    self._scrub(str(exc)),
                )
                handled = False
            if handled:
                return ToolResult(content=json.dumps({
                    "status": status,
                    "applied": True,
                    "manual_override": True,
                }))
            return ToolResult(content=json.dumps({
                "status": status,
                "applied": False,
                "note": "manual override delegate declined -- state recorded",
            }))
        self._desired_presence = status
        client = self._client
        if client is not None:
            try:
                await client.change_presence(status=status)
                return ToolResult(content=json.dumps({
                    "status": status, "applied": True,
                }))
            except Exception as exc:
                logger.warning(
                    "[discord_user] change_presence failed: %s",
                    self._scrub(str(exc)),
                )
                return ToolResult(content=json.dumps({
                    "status": status, "applied": False,
                    "note": "subscriber connected but change_presence "
                            "raised -- state recorded",
                }))
        return ToolResult(content=json.dumps({
            "status": status,
            "applied": False,
            "note": "subscriber not running -- status will apply on "
                    "next connect",
        }))

    # ------------------------------------------------------------------
    # Slice-2 internal-only send surface (Issue #177)
    # ------------------------------------------------------------------
    #
    # ``discord_send_message`` (above) is the LLM-callable surface. Its
    # ``confirm`` gate is the policy boundary for LLM-initiated sends:
    # the LLM proposes, the user/system confirms. Auto-reply is a DIFFERENT
    # policy decision -- the user has already opted in by putting the
    # sender on the allowlist (#177), and the gate exercise belongs to
    # the auto-reply controller (cerebral/discord_auto_reply.py), not
    # to each individual send. These two helpers expose the underlying
    # REST endpoints without the LLM-tool ceremony.

    async def trigger_typing(self, channel_id: str) -> None:
        """Emit a Discord typing indicator on a channel.
        Discord auto-decays the indicator after ~10s; the controller's
        delay distribution is clamped well below that, so one call per
        outbound reply is enough. Failure is non-fatal -- the indicator
        is a UX nicety, not a correctness gate."""
        if not channel_id:
            return
        token, err = self._resolve_token()
        if err is not None:
            return
        try:
            await self._request("POST", f"channels/{channel_id}/typing", token)
        except Exception as exc:
            logger.debug(
                "[discord_user] trigger_typing failed: %s",
                self._scrub(str(exc)),
            )

    async def apply_presence(self, status: str) -> bool:
        """Slice-3 internal presence-control surface used by the
        ``DiscordPresenceController``.

        Behaves like ``discord_set_presence`` except it bypasses the
        LLM-tool ceremony: presence transitions driven by the auto-
        presence state machine are NOT LLM-initiated, so the confirm
        gate doesn't apply (parity with slice 2's ``send_dm`` /
        ``trigger_typing``).

        Records the desired state (so the slice-1 next-connect path
        still works) and, when the subscriber is running, calls the
        underlying ``change_presence``. Returns True iff the call
        actually made it to the client. Failure is non-fatal: state is
        recorded for the next connect.
        """
        if status not in _VALID_PRESENCE:
            return False
        self._desired_presence = status
        client = self._client
        if client is None:
            return False
        try:
            await client.change_presence(status=status)
            return True
        except Exception as exc:
            logger.debug(
                "[discord_user] apply_presence failed: %s",
                self._scrub(str(exc)),
            )
            return False

    async def send_dm(self, channel_id: str, content: str) -> bool:
        """Slice-2 internal sender used by the auto-reply controller.

        Returns True iff the send was accepted by Discord. Logs (scrubbed)
        and returns False on any transport / HTTP failure -- the
        controller has already decided to send, so an exception bubbling
        up would leak past the per-channel lock and confuse the legacy
        draft-fallback path."""
        if not channel_id or not content:
            return False
        token, err = self._resolve_token()
        if err is not None:
            logger.warning(
                "[discord_user] auto-reply send aborted: %s", err,
            )
            return False
        try:
            await self._request(
                "POST", f"channels/{channel_id}/messages", token,
                json={"content": content},
            )
            return True
        except Exception as exc:
            logger.warning(
                "[discord_user] auto-reply send_dm failed: %s",
                self._scrub(str(exc)),
            )
            return False

    # ------------------------------------------------------------------
    # Subscriber lifecycle
    # ------------------------------------------------------------------

    @property
    def subscriber_running(self) -> bool:
        return self._client_task is not None and not self._client_task.done()

    async def start_subscriber(self) -> None:
        """Validate the token, build the client, register the message
        handler, and run ``client.start(token)`` as a background task.

        Safe to call when the token is missing or the client can't be
        built: logs the graceful-degradation line and returns. Cerebral
        keeps running.
        """
        if self.subscriber_running:
            logger.info(
                "[discord_user] subscriber already running -- ignoring "
                "start_subscriber",
            )
            return

        callback = self._resolve_draft_callback()
        if callback is None:
            logger.warning(
                "[discord_user] draft callback not wired -- subscriber "
                "not started",
            )
            return

        token, err = self._resolve_token()
        if err is not None:
            logger.warning(
                "[discord_user] not configured -- subscriber not "
                "started (%s)", err,
            )
            return

        # Validate the token against /users/@me. A bogus token here
        # fails fast with an actionable message rather than the WS
        # gateway closing cryptically a second later.
        try:
            who = await self._request("GET", "users/@me", token)
        except Exception as exc:
            logger.warning(
                "[discord_user] token validation failed (GET /users/@me): "
                "%s -- subscriber not started",
                self._scrub(str(exc)),
            )
            return
        username = ""
        if isinstance(who, dict):
            username = who.get("username") or ""
        logger.info(
            "[discord_user] Token validated for user=%s",
            username or "<unknown>",
        )

        # Build the gateway client.
        try:
            factory = self._client_factory_override or _default_client_factory
            client = factory()
        except Exception as exc:
            logger.warning(
                "[discord_user] client factory raised: %s -- "
                "subscriber not started",
                self._scrub(str(exc)),
            )
            return

        client.set_message_handler(self._build_inbound_handler(callback))
        self._client = client
        self._stop_event.clear()
        self._client_task = asyncio.create_task(self._run_client(token))
        logger.info(
            "[discord_user] Subscriber loop running (DMs only, draft-"
            "only inbound)",
        )

    async def stop_subscriber(self) -> None:
        """Cancel the client task and close the gateway connection.

        Idempotent.
        """
        self._stop_event.set()
        client = self._client
        task = self._client_task
        self._client_task = None
        self._client = None
        if client is not None:
            try:
                await client.close()
            except Exception as exc:  # pragma: no cover -- defensive
                logger.debug(
                    "[discord_user] client.close() ignored: %s",
                    self._scrub(str(exc)),
                )
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    async def _run_client(self, token: str) -> None:
        """Drive ``client.start(token)`` until ``stop_subscriber``."""
        client = self._client
        if client is None:
            return
        try:
            if self._desired_presence is not None:
                # Apply the user's stored set_presence wish on connect.
                # The real client may not accept change_presence before
                # start() resolves, so we wrap a best-effort attempt
                # AFTER start (via a side task).
                async def _apply_presence_when_ready() -> None:
                    try:
                        await client.change_presence(
                            status=self._desired_presence or "online",
                        )
                    except Exception as exc:
                        logger.debug(
                            "[discord_user] change_presence on connect "
                            "failed: %s", self._scrub(str(exc)),
                        )
                asyncio.create_task(_apply_presence_when_ready())
            await client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[discord_user] client.start raised: %s -- subscriber "
                "stopped", self._scrub(str(exc)),
            )

    def _build_inbound_handler(
        self, callback: DraftCallback,
    ) -> Callable[[Any], Awaitable[None]]:
        async def handler(message: Any) -> None:
            try:
                event = self._extract_event(message)
            except Exception as exc:  # pragma: no cover -- defensive
                logger.exception(
                    "[discord_user] inbound event extraction failed: %s",
                    self._scrub(str(exc)),
                )
                return
            if event is None:
                return
            try:
                await callback(event)
            except Exception as exc:
                logger.exception(
                    "[discord_user] draft callback raised: %s",
                    self._scrub(str(exc)),
                )

        return handler

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_event(self, message: Any) -> Optional[dict]:
        """Turn a discord.py-self Message into a draft event dict.

        Slice 1: DMs only (channel.type == private). Server messages
        are dropped silently -- handling them would invite mass-
        detection per the ToS risk in ADR-0006.

        Self-authored messages are also dropped (we don't want to draft
        a reply to ourselves).
        """
        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        if author is None or channel is None:
            return None

        # Drop messages we sent ourselves.
        if getattr(author, "bot", False) and not getattr(author, "self", False):
            # Other bots: ignore (we shouldn't be replying to bots either).
            return None
        # Self check via the channel's `me` if available; otherwise look
        # for an "is_self" flag the fake exposes for the test suite.
        if _is_self_author(author):
            return None

        # DMs only for v1. discord.py-self exposes channel.type as a
        # ChannelType enum whose `.name` is "private" for DMs and
        # "group" for group DMs; the fake in tests provides a plain
        # string.
        channel_type = _channel_type_name(channel)
        if channel_type not in ("private", "group"):
            return None

        channel_id = str(getattr(channel, "id", "") or "")
        author_id = str(getattr(author, "id", "") or "")
        author_name = str(
            getattr(author, "name", None)
            or getattr(author, "display_name", None)
            or ""
        )
        text = str(getattr(message, "content", "") or "")
        if not channel_id or not text:
            return None

        return {
            "channel": PLUGIN_NAME,
            "session_key": f"{PLUGIN_NAME}:dm:{channel_id}",
            "channel_id": channel_id,
            "channel_type": channel_type,
            "message_id": str(getattr(message, "id", "") or ""),
            "author_id": author_id,
            "author_name": author_name,
            "text": text,
        }

    def _resolve_token(self) -> tuple[Optional[str], Optional[str]]:
        """Return ``(token, error_msg)``. Exactly one is non-None."""
        provider = self._provider_override
        if provider is None and _token_provider_factory is not None:
            try:
                provider = _token_provider_factory()
            except Exception as exc:  # pragma: no cover -- defensive
                return None, f"token provider raised: {exc}"
        if provider is None:
            return None, _FACTORY_NOT_WIRED_MSG
        try:
            token = provider.current()
        except Exception as exc:  # pragma: no cover -- defensive
            return None, f"token provider raised: {exc}"
        if not token:
            return None, _NO_TOKEN_MSG
        self._seen_tokens.add(token)
        return token, None

    def _resolve_draft_callback(self) -> Optional[DraftCallback]:
        if self._draft_override is not None:
            return self._draft_override
        return _draft_callback

    async def _request(
        self, method: str, endpoint: str, token: str, *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> Any:
        # Discord user-account tokens are sent as ``Authorization: <token>``
        # (NO "Bearer " prefix -- that prefix is for OAuth bearer tokens).
        # The bot-API path would use ``Authorization: Bot <token>``, which
        # we deliberately don't use because this plugin is a self-bot.
        return await self._fetch(
            method,
            f"{_BASE}/{endpoint}",
            headers={"Authorization": token},
            params=params,
            json=json,
        )

    def _clamp_limit(self, raw: Any) -> int:
        try:
            value = int(raw) if raw is not None else _DEFAULT_LIMIT
        except (TypeError, ValueError):
            value = _DEFAULT_LIMIT
        return max(_MIN_LIMIT, min(_MAX_LIMIT, value))

    def _fail(self, tool_name: str, exc: Exception) -> ToolResult:
        scrubbed = self._scrub(str(exc))
        logger.error("[discord_user] %s failed: %s", tool_name, scrubbed)
        return ToolResult(
            content=self._scrub(f"Discord request failed: {exc}"),
            is_error=True,
        )

    async def _fire_activity(self) -> None:
        """Tick the slice-3 presence-activity callback when wired.
        Swallows exceptions so a misbehaving controller never breaks
        the LLM tool call (slice-3 fire-and-forget contract)."""
        cb = _activity_callback
        if cb is None:
            return
        try:
            await cb()
        except Exception as exc:
            logger.debug(
                "[discord_user] activity callback raised: %s",
                self._scrub(str(exc)),
            )

    def _scrub(self, text: str) -> str:
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text


# ---------------------------------------------------------------------------
# Shaping helpers -- flatten Discord REST payloads for the LLM
# ---------------------------------------------------------------------------

def _shape_conversation(ch: dict) -> dict:
    """Flatten a Discord channel object for the LLM."""
    recipients = ch.get("recipients") or []
    names: list[str] = []
    if isinstance(recipients, list):
        for r in recipients:
            if not isinstance(r, dict):
                continue
            name = r.get("global_name") or r.get("username") or ""
            if name:
                names.append(name)
    last_message_id = ch.get("last_message_id") or ""
    return {
        "id": str(ch.get("id", "") or ""),
        "type": _channel_type_label(ch.get("type")),
        "name": ch.get("name") or "",
        "recipient_names": names,
        "last_message_id": str(last_message_id) if last_message_id else "",
    }


def _shape_message(m: dict) -> dict:
    """Flatten a Discord message object for the LLM."""
    author = m.get("author") if isinstance(m.get("author"), dict) else {}
    author_name = (
        author.get("global_name")
        or author.get("username")
        or ""
    )
    return {
        "id": str(m.get("id", "") or ""),
        "channel_id": str(m.get("channel_id", "") or ""),
        "author_id": str(author.get("id", "") or ""),
        "author_name": author_name,
        "content": m.get("content", "") or "",
        "timestamp": m.get("timestamp", "") or "",
    }


def _encode_reaction(emoji: str) -> str:
    """URL-encode an emoji for Discord's reaction-path segment.

    Discord expects either a unicode emoji (e.g. a thumbs-up
    character) or ``name:id`` for custom emoji. ``urllib.parse.quote``
    with no safe characters is correct for both -- the colon in
    ``name:id`` is *not* reserved here (Discord parses the colon out
    of the percent-encoded form). The ``:`` is left as safe to keep
    test URLs readable while still encoding multi-byte unicode.
    """
    return urllib.parse.quote(emoji, safe=":")


def _channel_type_label(ctype: Any) -> str:
    """Map Discord's int channel-type codes to readable labels."""
    return {1: "dm", 3: "group_dm"}.get(ctype, str(ctype) if ctype else "")


def _channel_type_name(channel: Any) -> str:
    """Read a channel's type as a lowercase string.

    discord.py-self exposes ``channel.type`` as a ``ChannelType`` enum
    whose ``.name`` is e.g. ``"private"`` / ``"group"`` / ``"text"``.
    The fake in the test suite passes a plain string directly. Be
    permissive.
    """
    t = getattr(channel, "type", None)
    if t is None:
        return ""
    name = getattr(t, "name", None)
    if isinstance(name, str):
        return name.lower()
    return str(t).lower()


def _is_self_author(author: Any) -> bool:
    """Best-effort 'this message came from us' check.

    discord.py-self's Client.user is the logged-in user; a message's
    author equals that when the message is self-authored. Without
    importing the SDK we lean on duck-typed attributes the tests'
    fake supplies.
    """
    return bool(getattr(author, "is_self", False))


# ---------------------------------------------------------------------------
# Module-level lifecycle wrappers -- main.py drives these
# ---------------------------------------------------------------------------

_active_plugin: Optional[DiscordUserPlugin] = None


def create() -> DiscordUserPlugin:
    """Orchestrator entry point. Records the singleton so the
    module-level lifecycle wrappers can find it."""
    global _active_plugin
    _active_plugin = DiscordUserPlugin()
    return _active_plugin


async def start_subscriber() -> None:
    """Module-level start_subscriber the way main.py drives lifecycle on
    the orchestrator-loaded module instance (Issue #153 -- the same
    importlib-spec concern todoist.py's set_token_provider has)."""
    if _active_plugin is None:
        logger.warning(
            "[discord_user] start_subscriber called with no active "
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
