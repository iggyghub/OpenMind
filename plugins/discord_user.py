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

Slice 3 (Issue #178) adds three richer outbound tools:
  - ``discord_react``   -- add/remove an emoji reaction on a message.
  - ``discord_edit``    -- edit a message sent by the user's own account.
  - ``discord_delete``  -- delete a message owned by the user.
All three are ``irreversible=True`` and ``confirm``-gated like
``discord_send_message``.

Also adds an ``on_manual_override`` callback seam: when
``discord_set_presence`` is called manually (LLM or user), the plugin
notifies ``cerebral/discord_presence.py``'s ``DiscordPresenceController``
so the auto-presence machinery knows a manual override is in effect.

Architecture
------------

Two side-effect surfaces, both injectable:

  - ``fetch_fn`` (todoist.py precedent) -- raw HTTPS to
    ``discord.com/api/v10``. Drives the REST tools. Defaults to
    aiohttp -> httpx -> hard error.
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
_CONFIRM_REQUIRED_MSG_REACT = (
    "discord_react requires confirm=true to apply. "
    "Without it, returning the would-be reaction for review."
)
_CONFIRM_REQUIRED_MSG_EDIT = (
    "discord_edit requires confirm=true to apply. "
    "Without it, returning the would-be edit for review."
)
_CONFIRM_REQUIRED_MSG_DELETE = (
    "discord_delete requires confirm=true to delete. "
    "Without it, returning the would-be deletion for review."
)


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
        on_manual_override: Optional[Callable[[str], None]] = None,
    ) -> None:
        # Constructor injection wins over the module-level setters --
        # the todoist.py / openclaw_channels.py pattern.
        self._provider_override = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._client_factory_override = client_factory
        self._draft_override = draft_callback
        # Slice-3: called when discord_set_presence is invoked manually so
        # cerebral/discord_presence.py can mark the manual-override flag.
        self._on_manual_override = on_manual_override

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
                name="discord_set_presence",
                description=(
                    "Set the user's Discord presence status. Records "
                    "the desired status and applies it immediately when "
                    "the subscriber is connected. Manual call overrides "
                    "auto-presence until the next LLM Discord action."
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
            Tool(
                name="discord_react",
                description=(
                    "Add or remove an emoji reaction on a Discord DM "
                    "message. REQUIRES confirm=true to apply -- without "
                    "it, returns the would-be reaction for review."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Discord channel/DM id.",
                        },
                        "message_id": {
                            "type": "string",
                            "description": "Id of the message to react to.",
                        },
                        "emoji": {
                            "type": "string",
                            "description": (
                                "Unicode emoji (e.g. '\U0001f44d') or "
                                "custom emoji in name:id format."
                            ),
                        },
                        "action": {
                            "type": "string",
                            "enum": ["add", "remove"],
                            "description": "add or remove the reaction.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to apply. False (default) "
                                "returns the would-be reaction for review."
                            ),
                        },
                    },
                    "required": ["channel_id", "message_id", "emoji", "action"],
                },
                irreversible=True,
            ),
            Tool(
                name="discord_edit",
                description=(
                    "Edit a message previously sent by the user's own "
                    "Discord account. REQUIRES confirm=true to apply. "
                    "Errors cleanly when the target message is not owned "
                    "by the authenticated user (Discord enforces this at "
                    "the API level -- no client-side ownership check)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Discord channel/DM id.",
                        },
                        "message_id": {
                            "type": "string",
                            "description": "Id of the message to edit.",
                        },
                        "new_content": {
                            "type": "string",
                            "description": "New message text.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to apply. False (default) "
                                "returns the would-be edit for review."
                            ),
                        },
                    },
                    "required": ["channel_id", "message_id", "new_content"],
                },
                irreversible=True,
            ),
            Tool(
                name="discord_delete",
                description=(
                    "Delete a message owned by the authenticated user's "
                    "Discord account. REQUIRES confirm=true to delete. "
                    "Errors cleanly when the target message is not owned "
                    "by the authenticated user."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Discord channel/DM id.",
                        },
                        "message_id": {
                            "type": "string",
                            "description": "Id of the message to delete.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to delete. False (default) "
                                "returns the would-be deletion for review."
                            ),
                        },
                    },
                    "required": ["channel_id", "message_id"],
                },
                irreversible=True,
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
        if tool_name == "discord_set_presence":
            return await self._set_presence(args)
        if tool_name == "discord_react":
            return await self._react(args)
        if tool_name == "discord_edit":
            return await self._edit(args)
        if tool_name == "discord_delete":
            return await self._delete(args)
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

    async def _set_presence(self, args: dict) -> ToolResult:
        status = args.get("status")
        if status not in _VALID_PRESENCE:
            return ToolResult(
                content=(
                    f"invalid status: must be one of {_VALID_PRESENCE}"
                ),
                is_error=True,
            )
        self._desired_presence = status
        # Notify the presence controller that a manual override was set so
        # the auto-idle / auto-online machinery pauses until the next
        # LLM-driven Discord action (slice-3 seam).
        if self._on_manual_override is not None:
            self._on_manual_override(status)
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

    async def _react(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        message_id = args.get("message_id")
        emoji = args.get("emoji")
        action = args.get("action")
        for field, val in [
            ("channel_id", channel_id),
            ("message_id", message_id),
            ("emoji", emoji),
            ("action", action),
        ]:
            if not isinstance(val, str) or not val:
                return ToolResult(
                    content=f"missing required arg(s) for discord_react: "
                            f"{field}",
                    is_error=True,
                )
        if action not in ("add", "remove"):
            return ToolResult(
                content="discord_react: action must be 'add' or 'remove'",
                is_error=True,
            )
        if not args.get("confirm"):
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "message_id": message_id,
                "emoji": emoji,
                "action": action,
                "note": _CONFIRM_REQUIRED_MSG_REACT,
            }))

        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        encoded = urllib.parse.quote(str(emoji), safe=":")
        endpoint = (
            f"channels/{channel_id}/messages/{message_id}"
            f"/reactions/{encoded}/@me"
        )
        try:
            method = "PUT" if action == "add" else "DELETE"
            await self._request(method, endpoint, token)
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
        for field, val in [
            ("channel_id", channel_id),
            ("message_id", message_id),
            ("new_content", new_content),
        ]:
            if not isinstance(val, str) or not val:
                return ToolResult(
                    content=f"missing required arg(s) for discord_edit: "
                            f"{field}",
                    is_error=True,
                )
        if not args.get("confirm"):
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "message_id": message_id,
                "preview": new_content,
                "note": _CONFIRM_REQUIRED_MSG_EDIT,
            }))

        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)
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
        for field, val in [
            ("channel_id", channel_id),
            ("message_id", message_id),
        ]:
            if not isinstance(val, str) or not val:
                return ToolResult(
                    content=f"missing required arg(s) for discord_delete: "
                            f"{field}",
                    is_error=True,
                )
        if not args.get("confirm"):
            return ToolResult(content=json.dumps({
                "confirmed": False,
                "channel_id": channel_id,
                "message_id": message_id,
                "note": _CONFIRM_REQUIRED_MSG_DELETE,
            }))

        token, err = self._resolve_token()
        if err is not None:
            return ToolResult(content=err, is_error=True)
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
