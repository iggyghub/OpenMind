"""
Google Calendar MCP plugin -- Issue #117, ADR-0005.

Two tools, both hitting the **real Google Calendar API**, authorized by the
active profile's OAuth **access token** read from the #112 credential store
(refreshed on demand via #113):

  - ``calendar_list_events`` -- ``events.list`` over the primary calendar,
    optionally time-bounded. Read-only.
  - ``calendar_create_event`` -- ``events.insert`` against the primary
    calendar. Write (ask-class ``external_data_write``).

This closes the Gmail/Calendar real-API arc: same spine as ``plugins/gmail.py``
(#115 + #116) -- token from the per-profile credential store (#112), one
401->refresh->retry, scrub, injectable ``fetch_fn`` + module-level
``set_token_provider`` seam, ``GmailAPIError`` analogue ``CalendarAPIError``.
The only differences are the API host + the two endpoint shapes.

This is the **real Google Calendar API path, OAuth bearer from the
per-profile credential store (#112), not the n8n bridge**. Runtime priority
is real-API -> existing n8n bridge -> OSS fallback (ADR-0004 consequence;
the ``plugins/google_workspace.py`` bridge stays intact as the fallback tier
-- no new ADR, no CONTEXT.md/ADR-0005 change; the docs rode #112). The
``calendar`` scope is already in ``cerebral/main.py``'s ``_GOOGLE_SCOPES``
union (#114) -- no scope change.

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/gmail.py`` precedent (the orchestrator calls ``module.create()``
zero-arg, so a real token can only arrive this way). Tests bypass it by
passing a stub provider + stub ``fetch_fn`` to the constructor: no real
network, OAuth, keyring or browser in the suite.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "calendar"

# ADR-0005 / Issue #117.
#   - external_data_read + network_egress_cloud: calendar_list_events fetches
#     the user's events from www.googleapis.com over the internet (the
#     gmail.py/youtube.py/reddit.py surface). network_egress_cloud is what
#     the AST audit actually requires (aiohttp/httpx call sites ->
#     NETWORK_EGRESS_ANY, call_site_capabilities.py:148-167).
#   - secrets_read is a DELIBERATE over-declaration -- the gmail.py /
#     youtube.py posture-B precedent (clone it, do NOT contrast it). The AST
#     audit maps secrets_read ONLY to keyring.get_password/set_password
#     (call_site_capabilities.py:187-188) and is per-file/intraprocedural.
#     This plugin calls provider.current()/refresh() -- never keyring.*
#     directly (the keyring read lives in cerebral/db/credentials.py, an
#     unscanned file), so the audit will NOT auto-require secrets_read here.
#     We declare it anyway because the plugin's job is to surface the user's
#     calendar behind an OAuth credential, and handing that a silent-class
#     free pass is the wrong default (ADR-0005 threats T1/T4). Do not "tidy
#     this away" -- over-declaration is intentional and audit-safe (_inspect
#     only fails on *under*-declaration).
#   - external_data_write (Issue #117): calendar_create_event mutates an
#     external account (it creates a calendar event via events.insert). This
#     is the correct *required* ask-class semantic class (ADR-0005 day-1
#     ACL, line 34) -- NOT an over-declaration like secrets_read above.
#     Like external_data_read it is hand-declared: external_data_* is
#     absent from the AST capability map AND the bare-attr fallback
#     (call_site_capabilities.py:148-199 maps only fs/clipboard/network/
#     secrets/screen/device/code primitives), so the per-file AST audit
#     never auto-requires it -- a semantic capability declared because the
#     tool's effect IS the write, audit-safe.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"
_DEFAULT_LIMIT = 10

_FACTORY_NOT_WIRED_MSG = "Calendar is not available -- token provider not wired"
_NO_ACCOUNT_MSG = "no Google account connected"
_MISSING_CREATE_ARG_MSG = "missing required arg(s) for calendar_create_event: {}"


class TokenProvider(Protocol):
    """Per-active-profile bearer-token handle wired from main.py.

    ``current()`` returns the stored access token (no network) or ``None``;
    ``refresh()`` exchanges the stored refresh token for a fresh access
    token via #113 (the 401 path) and returns it.
    """

    def current(self) -> Optional[str]: ...
    def refresh(self) -> str: ...


# Factory returns ``TokenProvider | None`` -- ``None`` when no profile is
# active or the active profile has no connected Google account. Set once at
# startup by cerebral/main.py via set_token_provider(), mirroring
# plugins/gmail.py's set_token_provider. Tests pass a provider directly to
# the constructor (constructor injection wins).
TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_calendar_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when no
    profile is loaded or the active profile has no connected Google
    account, a fresh handle bound to the active profile otherwise. (The
    active profile can change between calls; the factory re-resolves each
    time.)
    """
    global _token_provider_factory
    _token_provider_factory = fn


class CalendarAPIError(RuntimeError):
    """Any transport/HTTP failure of a Calendar call. ``status`` is the
    HTTP status code when one is available (so the 401 -> refresh -> retry
    path can branch on it), else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    HTTP errors are mapped to CalendarAPIError carrying the status code so
    the caller can branch on 401; transport/other errors carry status=None.
    Deps lazy-imported here so module import stays stdlib-only (learning
    #12).
    """
    try:
        import aiohttp  # type: ignore
    except ImportError:
        aiohttp = None  # type: ignore
    if aiohttp is not None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers,
                                            params=params, json=json) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise CalendarAPIError(str(exc), status=exc.status) from exc
        except CalendarAPIError:
            raise
        except Exception as exc:
            raise CalendarAPIError(str(exc), status=None) from exc

    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None  # type: ignore
    if httpx is not None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, headers=headers,
                                            params=params, json=json)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise CalendarAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except CalendarAPIError:
            raise
        except Exception as exc:
            raise CalendarAPIError(str(exc), status=None) from exc

    raise CalendarAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _event_time(value: Any) -> str:
    """Unwrap a Calendar API event time object to a flat ISO 8601 string.

    The API returns ``{"dateTime": "...", "timeZone": "..."}`` for timed
    events and ``{"date": "YYYY-MM-DD"}`` for all-day events. The plugin
    surfaces a single flat string in either case; absent -> empty string.
    """
    if not isinstance(value, dict):
        return ""
    return value.get("dateTime", "") or value.get("date", "") or ""


def _shape_event(evt: Any) -> dict:
    """Flatten one ``events.list``/``events.get`` response row."""
    if not isinstance(evt, dict):
        return {}
    raw_attendees = evt.get("attendees") or []
    attendees = [
        a.get("email", "") for a in raw_attendees
        if isinstance(a, dict) and a.get("email")
    ]
    return {
        "id": evt.get("id", ""),
        "summary": evt.get("summary", ""),
        "start": _event_time(evt.get("start")),
        "end": _event_time(evt.get("end")),
        "location": evt.get("location", ""),
        "attendees": attendees,
    }


def _build_event_body(title: str, start: str, end: str | None,
                      description: str | None,
                      attendees: list[str] | None) -> dict:
    """Build the ``events.insert`` JSON body from flat tool args. ``start``
    + ``end`` are ISO 8601 strings carrying their own offset/Z; the API's
    timeZone default applies for offset-less inputs."""
    body: dict = {
        "summary": title,
        "start": {"dateTime": start},
    }
    body["end"] = {"dateTime": end if end else start}
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees if e]
    return body


class CalendarPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        # Constructor-injected provider wins over the module-level setter.
        # Tests pass directly; production leaves this None and main.py wires
        # the module-level _token_provider_factory at startup.
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        # Every bearer token ever held this call, so _scrub strips it from
        # any log line / ToolResult even across a refresh.
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="calendar_list_events",
                description=(
                    "List upcoming events from the active profile's primary "
                    "Google Calendar, optionally bounded by a time window. "
                    "Returns event id, summary, start, end, location and "
                    "attendees."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "from": {
                            "type": "string",
                            "description": "Start of window (ISO 8601).",
                        },
                        "to": {
                            "type": "string",
                            "description": "End of window (ISO 8601).",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum events to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="calendar_create_event",
                description=(
                    "Create an event on the active profile's primary Google "
                    "Calendar via the real Google Calendar API. Returns the "
                    "created event id, html link and status."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Event title.",
                        },
                        "start": {
                            "type": "string",
                            "description": "Start datetime (ISO 8601).",
                        },
                        "end": {
                            "type": "string",
                            "description": (
                                "End datetime (ISO 8601, optional)."
                            ),
                        },
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of attendee email addresses (optional)."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Event description (optional).",
                        },
                    },
                    "required": ["title", "start"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "calendar_list_events":
            return await self._list(args)
        if tool_name == "calendar_create_event":
            return await self._create(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the gmail.py
        / youtube.py posture)."""
        factory = self._token_factory()
        if self._provider is not None:
            return self._provider, None
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        provider = factory()
        if provider is None:
            return None, _NO_ACCOUNT_MSG
        return provider, None

    @staticmethod
    def _token_factory() -> Optional[TokenProviderFactory]:
        return _token_provider_factory

    def _scrub(self, text: str) -> str:
        """Strip every bearer token seen this call from text bound for a log
        or ToolResult."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider, *, force: bool) -> str:
        """Get a bearer token: refresh on ``force`` (the 401 path) or when
        none is stored, else the stored token. Records it for scrubbing."""
        token = None if force else provider.current()
        if not token:
            token = provider.refresh()
        if token:
            self._seen_tokens.add(token)
        return token or ""

    async def _get_events(self, token: str,
                          params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/events",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _post_event(self, token: str, body: dict) -> Any:
        return await self._fetch(
            "POST",
            f"{_BASE}/events",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _run_list(self, token: str, params: dict,
                        max_results: int) -> list[dict]:
        listing = await self._get_events(token, params=params)
        if not isinstance(listing, dict):
            raise CalendarAPIError(
                "unexpected Calendar list response", status=None
            )
        items = [
            evt for evt in (listing.get("items") or [])
            if isinstance(evt, dict)
        ][:max_results]
        return [_shape_event(evt) for evt in items]

    async def _list(self, args: dict) -> ToolResult:
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params: dict = {
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        time_min = args.get("from")
        if isinstance(time_min, str) and time_min:
            params["timeMin"] = time_min
        time_max = args.get("to")
        if isinstance(time_max, str) and time_max:
            params["timeMax"] = time_max

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                events = await self._run_list(token, params, max_results)
            except CalendarAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                events = await self._run_list(token, params, max_results)
        except Exception as exc:
            logger.error(
                "[calendar] calendar_list_events failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Calendar list failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({"events": events}))

    async def _create(self, args: dict) -> ToolResult:
        missing = [
            name
            for name in ("title", "start")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_CREATE_ARG_MSG.format(", ".join(missing)),
                is_error=True,
            )
        end = args.get("end")
        end = end if isinstance(end, str) and end else None
        description = args.get("description")
        description = (
            description if isinstance(description, str) and description
            else None
        )
        raw_attendees = args.get("attendees")
        if isinstance(raw_attendees, list):
            attendees = [
                a for a in raw_attendees
                if isinstance(a, str) and a
            ]
        else:
            attendees = None
        body = _build_event_body(
            args["title"], args["start"], end, description, attendees,
        )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._post_event(token, body)
            except CalendarAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                resp = await self._post_event(token, body)
        except Exception as exc:
            logger.error(
                "[calendar] calendar_create_event failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Calendar create failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Calendar create response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "html_link": resp.get("htmlLink", ""),
            "status": "created",
        }))


def create(fetch_fn: FetchFn | None = None) -> CalendarPlugin:
    return CalendarPlugin(fetch_fn=fetch_fn)
