"""
Google Docs MCP plugin -- Issue #224, ADR-0005.

Four tools, hitting the **real Google Docs API** (and Drive API for
listing), authorized by the active profile's OAuth **access token** read
from the #112 credential store (refreshed on demand via #113):

  - ``docs_list``   -- Drive API ``files.list`` filtered to Docs MIME type.
                       Read-only.
  - ``docs_read``   -- Docs API ``documents.get``; extracts body text.
                       Read-only.
  - ``docs_create`` -- Docs API ``documents.create``. Write (ask-class
                       ``external_data_write``).
  - ``docs_append`` -- Docs API ``documents.batchUpdate`` with insertText at
                       end of document. Write (ask-class
                       ``external_data_write``).

This is the **real Google Docs API path, OAuth bearer from the per-profile
credential store (#112), not the n8n bridge**. Runtime priority is
real-API -> existing n8n bridge -> OSS fallback (ADR-0004 consequence; the
``plugins/google_workspace.py`` bridge stays intact as the fallback tier --
no new ADR, no CONTEXT.md change). The ``documents`` + ``drive.readonly``
scopes are added to ``cerebral/main.py``'s ``_GOOGLE_SCOPES`` union
alongside gmail/calendar.

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/gmail.py`` / ``plugins/calendar.py`` precedent. Tests bypass it
by passing a stub provider + stub ``fetch_fn`` to the constructor: no real
network, OAuth, keyring or browser in the suite.

**No live-verify in this slice.** Live verification is batched into slice
V.1 (#239) so the autonomous loop is never blocked on browser OAuth consent.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_docs"

# ADR-0005 / Issue #224.
#   - external_data_read + network_egress_cloud: docs_list / docs_read fetch
#     data from googleapis.com over the internet (the gmail.py / calendar.py
#     surface). network_egress_cloud is what the AST audit actually requires
#     (aiohttp/httpx call sites -> NETWORK_EGRESS_ANY,
#     call_site_capabilities.py:162-164).
#   - secrets_read is a DELIBERATE over-declaration -- the gmail.py /
#     calendar.py posture-B precedent (clone it, do NOT contrast it). The
#     AST audit maps secrets_read ONLY to keyring.get_password/set_password
#     and is per-file/intraprocedural. This plugin calls
#     provider.current()/refresh() -- never keyring.* directly (the keyring
#     read lives in cerebral/db/credentials.py, an unscanned file), so the
#     audit will NOT auto-require secrets_read here. We declare it anyway
#     because the plugin's job is to surface the user's documents behind an
#     OAuth credential (ADR-0005 threats T1/T4). Do not "tidy this away" --
#     over-declaration is intentional and audit-safe.
#   - external_data_write (docs_create, docs_append): these tools mutate an
#     external account. Hand-declared: external_data_* is absent from the
#     AST capability map AND the bare-attr fallback.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_DOCS_BASE = "https://docs.googleapis.com/v1/documents"
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_DOCS_MIME = "application/vnd.google-apps.document"
_DEFAULT_LIMIT = 10

_FACTORY_NOT_WIRED_MSG = (
    "Google Docs is not available -- token provider not wired"
)
_NO_ACCOUNT_MSG = "no Google account connected"
_MISSING_CREATE_ARG_MSG = "missing required arg(s) for docs_create: {}"
_MISSING_APPEND_ARG_MSG = "missing required arg(s) for docs_append: {}"
_MISSING_READ_ARG_MSG = "'document_id' is required for docs_read"


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
    """Wire main.py's _get_google_docs_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when no
    profile is loaded or the active profile has no connected Google
    account, a fresh handle bound to the active profile otherwise.
    """
    global _token_provider_factory
    _token_provider_factory = fn


class DocsAPIError(RuntimeError):
    """Any transport/HTTP failure of a Docs or Drive call. ``status`` is
    the HTTP status code when available (so the 401 -> refresh -> retry
    path can branch on it), else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    HTTP errors are mapped to DocsAPIError carrying the status code so the
    caller can branch on 401; transport/other errors carry status=None.
    Deps lazy-imported here so module import stays stdlib-only.
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
            raise DocsAPIError(str(exc), status=exc.status) from exc
        except DocsAPIError:
            raise
        except Exception as exc:
            raise DocsAPIError(str(exc), status=None) from exc

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
            raise DocsAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except DocsAPIError:
            raise
        except Exception as exc:
            raise DocsAPIError(str(exc), status=None) from exc

    raise DocsAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _extract_text(body: Any) -> str:
    """Extract plain text from a Docs API document body object.

    Iterates body.content -> paragraph.elements -> textRun.content and
    concatenates all text runs. Non-paragraph structural elements
    (sectionBreak, tableOfContents, table, etc.) are skipped.
    """
    if not isinstance(body, dict):
        return ""
    parts: list[str] = []
    for elem in (body.get("content") or []):
        if not isinstance(elem, dict):
            continue
        para = elem.get("paragraph")
        if not isinstance(para, dict):
            continue
        for pe in (para.get("elements") or []):
            if not isinstance(pe, dict):
                continue
            run = pe.get("textRun")
            if isinstance(run, dict):
                text = run.get("content") or ""
                if text:
                    parts.append(text)
    return "".join(parts)


def _shape_file(f: Any) -> dict:
    """Flatten one Drive files.list entry to the canonical wire shape."""
    if not isinstance(f, dict):
        return {}
    return {
        "id": f.get("id", ""),
        "name": f.get("name", ""),
        "modified_time": f.get("modifiedTime", ""),
        "web_view_link": f.get("webViewLink", ""),
    }


class GoogleDocsPlugin:
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
                name="docs_list",
                description=(
                    "List recent Google Docs documents from the active "
                    "profile's Google Drive, ordered by last modified time. "
                    "Returns document id, name, modified time, and web view "
                    "link. Read-only."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum documents to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="docs_read",
                description=(
                    "Read the plain-text body of a Google Docs document by "
                    "its document ID via the real Google Docs API. Returns "
                    "document id, title, and the full plain-text body. "
                    "Read-only."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Google Docs document ID.",
                        },
                    },
                    "required": ["document_id"],
                },
            ),
            Tool(
                name="docs_create",
                description=(
                    "Create a new Google Docs document in the active "
                    "profile's Google Drive via the real Google Docs API. "
                    "Returns the new document id, title, and revision id."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Title of the new document.",
                        },
                    },
                    "required": ["title"],
                },
            ),
            Tool(
                name="docs_append",
                description=(
                    "Append text to the end of an existing Google Docs "
                    "document via the Docs API batchUpdate endpoint. Returns "
                    "the document id, revision id, and status."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,
                schema={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Google Docs document ID.",
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to append to the document.",
                        },
                    },
                    "required": ["document_id", "text"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "docs_list":
            return await self._list(args)
        if tool_name == "docs_read":
            return await self._read(args)
        if tool_name == "docs_create":
            return await self._create(args)
        if tool_name == "docs_append":
            return await self._append(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the gmail.py
        / calendar.py posture).
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

    async def _get(self, url: str, token: str,
                   params: dict | None = None) -> Any:
        return await self._fetch(
            "GET", url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _post(self, url: str, token: str, body: dict) -> Any:
        return await self._fetch(
            "POST", url,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _run_list(self, token: str, max_results: int) -> list[dict]:
        resp = await self._get(
            _DRIVE_FILES, token,
            params={
                "q": f"mimeType='{_DOCS_MIME}'",
                "orderBy": "modifiedTime desc",
                "pageSize": max_results,
                "fields": "files(id,name,modifiedTime,webViewLink)",
            },
        )
        if not isinstance(resp, dict):
            raise DocsAPIError("unexpected Drive list response", status=None)
        return [
            _shape_file(f)
            for f in (resp.get("files") or [])
            if isinstance(f, dict)
        ][:max_results]

    async def _list(self, args: dict) -> ToolResult:
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                docs = await self._run_list(token, max_results)
            except DocsAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                docs = await self._run_list(token, max_results)
        except Exception as exc:
            logger.error(
                "[google_docs] docs_list failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"Docs list failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({"documents": docs}))

    async def _run_read(self, token: str, document_id: str) -> dict:
        resp = await self._get(f"{_DOCS_BASE}/{document_id}", token)
        if not isinstance(resp, dict):
            raise DocsAPIError("unexpected Docs read response", status=None)
        return resp

    async def _read(self, args: dict) -> ToolResult:
        document_id = args.get("document_id")
        if not document_id or not isinstance(document_id, str):
            return ToolResult(content=_MISSING_READ_ARG_MSG, is_error=True)

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._run_read(token, document_id)
            except DocsAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                resp = await self._run_read(token, document_id)
        except Exception as exc:
            logger.error(
                "[google_docs] docs_read failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"Docs read failed: {exc}"),
                is_error=True,
            )

        body_text = _extract_text(resp.get("body"))
        return ToolResult(content=json.dumps({
            "document_id": resp.get("documentId", document_id),
            "title": resp.get("title", ""),
            "body": body_text,
        }))

    async def _run_create(self, token: str, title: str) -> dict:
        resp = await self._post(_DOCS_BASE, token, {"title": title})
        if not isinstance(resp, dict):
            raise DocsAPIError("unexpected Docs create response", status=None)
        return resp

    async def _create(self, args: dict) -> ToolResult:
        missing = [
            name for name in ("title",)
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_CREATE_ARG_MSG.format(", ".join(missing)),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._run_create(token, args["title"])
            except DocsAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                resp = await self._run_create(token, args["title"])
        except Exception as exc:
            logger.error(
                "[google_docs] docs_create failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Docs create failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({
            "document_id": resp.get("documentId", ""),
            "title": resp.get("title", ""),
            "revision_id": resp.get("revisionId", ""),
        }))

    async def _run_append(self, token: str, document_id: str,
                          text: str) -> dict:
        body = {
            "requests": [{
                "insertText": {
                    "endOfSegmentLocation": {},
                    "text": text,
                },
            }],
        }
        resp = await self._post(
            f"{_DOCS_BASE}/{document_id}:batchUpdate", token, body
        )
        if not isinstance(resp, dict):
            raise DocsAPIError(
                "unexpected Docs batchUpdate response", status=None
            )
        return resp

    async def _append(self, args: dict) -> ToolResult:
        missing = [
            name for name in ("document_id", "text")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_APPEND_ARG_MSG.format(", ".join(missing)),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._run_append(
                    token, args["document_id"], args["text"]
                )
            except DocsAPIError as exc:
                if exc.status != 401:
                    raise
                # One 401 -> refresh -> retry only; no backoff loop.
                token = self._take_token(provider, force=True)
                resp = await self._run_append(
                    token, args["document_id"], args["text"]
                )
        except Exception as exc:
            logger.error(
                "[google_docs] docs_append failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Docs append failed: {exc}"),
                is_error=True,
            )

        write_control = resp.get("writeControl") or {}
        return ToolResult(content=json.dumps({
            "document_id": resp.get("documentId", args["document_id"]),
            "revision_id": write_control.get("requiredRevisionId", ""),
            "status": "appended",
        }))


def create(fetch_fn: FetchFn | None = None) -> GoogleDocsPlugin:
    return GoogleDocsPlugin(fetch_fn=fetch_fn)
