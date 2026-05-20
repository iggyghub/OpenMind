"""
Gmail MCP plugin -- Issues #115 / #116, ADR-0005.

Two tools, both hitting the **real Gmail API**, authorized by the active
profile's OAuth **access token** read from the #112 credential store
(refreshed on demand via #113):

  - ``gmail_search`` (#115) -- ``users.messages.list`` + ``users.messages.get``
    in ``metadata`` format. Read-only.
  - ``gmail_send`` (#116) -- ``users.messages.send`` with an RFC822 /
    base64url ``{"raw": ...}`` body. Write (ask-class
    ``external_data_write``).

#115 is the end-to-end tracer bullet: #112 store -> #113 OAuth token ->
real ``gmail.googleapis.com`` call -> shaped ``ToolResult``. #116 reuses
that exact spine (same token seam, same one-401->refresh->retry, same
scrub) -- the only new surface is the RFC822/base64url build + the
``users.messages.send`` POST.

This is the **real Gmail API path, OAuth bearer from the per-profile
credential store (#112), not the n8n bridge**. Runtime priority is
real-API -> existing n8n bridge -> OSS fallback (ADR-0004 consequence; the
``plugins/google_workspace.py`` bridge stays intact as the fallback tier --
no new ADR, no CONTEXT.md/ADR-0005 change; the docs rode #112).

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/memory.py`` ``set_memory_factory`` precedent (the orchestrator
calls ``module.create()`` zero-arg, so a real token can only arrive this
way). Tests bypass it by passing a stub provider + stub ``fetch_fn`` to the
constructor: no real network, OAuth, keyring or browser in the suite.
"""
from __future__ import annotations

import base64
import json
import logging
from email.message import EmailMessage
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "gmail"

# ADR-0005 / Issues #115, #116.
#   - external_data_read + network_egress_cloud: gmail_search fetches the
#     user's mail from gmail.googleapis.com over the internet (the
#     youtube.py/reddit.py surface). network_egress_cloud is what the AST
#     audit actually requires (aiohttp/httpx call sites -> NETWORK_EGRESS_ANY,
#     call_site_capabilities.py:162-164).
#   - secrets_read is a DELIBERATE over-declaration -- the youtube.py
#     posture-B precedent (clone it, do NOT contrast it). The AST audit maps
#     secrets_read ONLY to keyring.get_password/set_password
#     (call_site_capabilities.py:187-188) and is per-file/intraprocedural.
#     This plugin calls provider.current()/refresh() -- never keyring.*
#     directly (the keyring read lives in cerebral/db/credentials.py, an
#     unscanned file), so the audit will NOT auto-require secrets_read here.
#     We declare it anyway because the plugin's job is to surface the user's
#     mail behind an OAuth credential, and handing that a silent-class free
#     pass is the wrong default (ADR-0005 threats T1/T4). Do not "tidy this
#     away" -- over-declaration is intentional and audit-safe (_inspect only
#     fails on *under*-declaration).
#   - external_data_write (Issue #116): gmail_send mutates an external
#     account (it sends mail via users.messages.send). This is the correct
#     *required* ask-class semantic class (ADR-0005 day-1 ACL, line 34) --
#     NOT an over-declaration like secrets_read above. Like external_data_read
#     it is hand-declared: external_data_* is absent from the AST capability
#     map AND the bare-attr fallback (call_site_capabilities.py:148-199 maps
#     only fs/clipboard/network/secrets/screen/device/code primitives), so
#     the per-file AST audit never auto-requires it -- a semantic capability
#     declared because the tool's effect IS the write, audit-safe.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_DEFAULT_LIMIT = 10
_METADATA_HEADERS = ("From", "To", "Subject", "Date")

_FACTORY_NOT_WIRED_MSG = "Gmail is not available -- token provider not wired"
_NO_ACCOUNT_MSG = "no Google account connected"
_BLANK_QUERY_MSG = "'query' is required for gmail_search"
_MISSING_SEND_ARG_MSG = "missing required arg(s) for gmail_send: {}"


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
# plugins/memory.py's set_memory_factory. Tests pass a provider directly to
# the constructor (constructor injection wins).
TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_gmail_token_provider() -- called once at startup
    after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when no
    profile is loaded or the active profile has no connected Google
    account, a fresh handle bound to the active profile otherwise. (The
    active profile can change between calls; the factory re-resolves each
    time.)
    """
    global _token_provider_factory
    _token_provider_factory = fn


class GmailAPIError(RuntimeError):
    """Any transport/HTTP failure of a Gmail call. ``status`` is the HTTP
    status code when one is available (so the 401 -> refresh -> retry path
    can branch on it), else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    HTTP errors are mapped to GmailAPIError carrying the status code so the
    caller can branch on 401; transport/other errors carry status=None.
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
            raise GmailAPIError(str(exc), status=exc.status) from exc
        except GmailAPIError:
            raise
        except Exception as exc:
            raise GmailAPIError(str(exc), status=None) from exc

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
            raise GmailAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except GmailAPIError:
            raise
        except Exception as exc:
            raise GmailAPIError(str(exc), status=None) from exc

    raise GmailAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _header(headers: Any, name: str) -> str:
    """Case-insensitively read one header value from a Gmail
    ``payload.headers`` list of ``{"name","value"}`` dicts."""
    if not isinstance(headers, list):
        return ""
    target = name.lower()
    for h in headers:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == target:
            return h.get("value", "") or ""
    return ""


def _shape_message(msg: Any) -> dict:
    """Flatten one ``users.messages.get`` (metadata format) response.

    Raw passthrough -- no date parsing, no body/attachment fetch.
    """
    if not isinstance(msg, dict):
        return {}
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "snippet": msg.get("snippet", ""),
    }


def _build_raw(to: str, subject: str, body: str, cc: str | None) -> str:
    """Build an RFC822 message (stdlib ``email``) and base64url-encode it
    for the Gmail ``users.messages.send`` ``{"raw": ...}`` body."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


class GmailPlugin:
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
                name="gmail_search",
                description=(
                    "Search the active profile's Gmail using Gmail search "
                    "syntax (e.g. 'from:alice is:unread newer_than:7d'). "
                    "Returns message id, thread id, from, to, subject, date "
                    "and snippet. Read-only; does not fetch bodies or "
                    "attachments."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Gmail search query.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum messages to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="gmail_send",
                description=(
                    "Send an email from the active profile's Gmail account "
                    "via the real Gmail API. Plain-text body. Returns the "
                    "sent message id, thread id and status."
                ),
                plugin=PLUGIN_NAME,
                # Issue #139 — first marked irreversible tool. The orchestrator
                # ORs this into ``CallFlags`` at dispatch so a queued send-mail
                # candidate routes through the modal even when a Session or
                # Persistent grant covers ``external_data_write``.
                irreversible=True,
                schema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address.",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Email body (plain text).",
                        },
                        "cc": {
                            "type": "string",
                            "description": "CC email address (optional).",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "gmail_search":
            return await self._search(args)
        if tool_name == "gmail_send":
            return await self._send(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the youtube.py
        missing-key / memory.py factory-not-wired posture).
        """
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
        or ToolResult. aiohttp/httpx status errors can embed request detail,
        and we set the token in a header, so scrub defensively."""
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

    async def _request(self, endpoint: str, token: str,
                       params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _run_search(self, token: str, query: str,
                          max_results: int) -> list[dict]:
        listing = await self._request(
            "messages", token,
            params={"q": query, "maxResults": max_results},
        )
        if not isinstance(listing, dict):
            raise GmailAPIError("unexpected Gmail list response", status=None)
        ids = [
            m.get("id")
            for m in (listing.get("messages") or [])
            if isinstance(m, dict) and m.get("id")
        ][:max_results]
        out: list[dict] = []
        for mid in ids:
            msg = await self._request(
                f"messages/{mid}", token,
                params={
                    "format": "metadata",
                    "metadataHeaders": list(_METADATA_HEADERS),
                },
            )
            out.append(_shape_message(msg))
        return out

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query or not isinstance(query, str):
            return ToolResult(content=_BLANK_QUERY_MSG, is_error=True)
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                results = await self._run_search(token, query, max_results)
            except GmailAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                results = await self._run_search(token, query, max_results)
        except Exception as exc:
            logger.error(
                "[gmail] gmail_search failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"Gmail search failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({"results": results}))

    async def _post_send(self, token: str, raw: str) -> Any:
        """POST the base64url RFC822 message to users.messages.send.

        Sibling of _request (which is GET-only): reuses self._fetch -- the
        default transport already accepts method + a json body -- with the
        same Bearer header. No new transport.
        """
        return await self._fetch(
            "POST",
            f"{_BASE}/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw},
        )

    async def _send(self, args: dict) -> ToolResult:
        missing = [
            name
            for name in ("to", "subject", "body")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_SEND_ARG_MSG.format(", ".join(missing)),
                is_error=True,
            )
        cc = args.get("cc")
        cc = cc if isinstance(cc, str) and cc else None
        raw = _build_raw(args["to"], args["subject"], args["body"], cc)

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._post_send(token, raw)
            except GmailAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                resp = await self._post_send(token, raw)
        except Exception as exc:
            logger.error(
                "[gmail] gmail_send failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"Gmail send failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Gmail send response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "thread_id": resp.get("threadId", ""),
            "status": "sent",
        }))


def create(fetch_fn: FetchFn | None = None) -> GmailPlugin:
    return GmailPlugin(fetch_fn=fetch_fn)
