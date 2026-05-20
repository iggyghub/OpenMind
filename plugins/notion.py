"""
Notion MCP plugin -- Issue #136, ADR-0005.

Four tools, all hitting the **real Notion REST API v1**, authorized by
a STATIC Internal Integration Token read from the ``NOTION_API_TOKEN``
environment variable via the provider seam:

  - ``notion_search`` -- ``POST /v1/search`` with a query string and
    optional ``filter_type`` ("page" or "database") + page_size +
    start_cursor for pagination. Read-only.
  - ``notion_retrieve_page`` -- ``GET /v1/pages/{id}``. Returns page
    metadata + properties (title, url, parent, timestamps,
    archived). Read-only.
  - ``notion_retrieve_block_children`` -- ``GET /v1/blocks/{id}/children``
    with page_size + start_cursor. Returns one page of block-children
    with plain-text extracted from each block's ``rich_text``.
    Read-only.
  - ``notion_create_page`` -- ``POST /v1/pages`` with parent
    ``{page_id: ...}``, title property and optional content blocks
    built from a string split on ``\\n\\n``. Write (ask-class
    ``external_data_write``).

Clones the ``plugins/todoist.py`` spine: same ``Authorization: Bearer``
header transport, same injectable ``fetch_fn`` + module-level
``set_token_provider`` seam, same scrub. The one structural divergence
is the ``Notion-Version`` header, mandatory on every Notion API call --
``_request`` adds ``Notion-Version: _NOTION_VERSION`` alongside the
bearer header on every call.

Like Todoist, Notion's Internal Integration Token is STATIC
(user-rotated from the Notion workspace settings, not OAuth) so the
``TokenProvider`` Protocol carries **only** ``current()``. There is no
refresh capability to describe and no 401->refresh->retry path in the
plugin: a 401 propagates straight through as an error and the user
rotates the env var manually.

The token reaches the plugin via a module-level ``set_token_provider``
setter wired from ``cerebral/main.py`` -- the exact ``plugins/todoist.py``
precedent (the orchestrator calls ``module.create()`` zero-arg, so a
real token can only arrive this way). Tests bypass it by passing a
stub provider + stub ``fetch_fn`` to the constructor: no real
network, OAuth, keyring or browser in the suite.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "notion"

# ADR-0005 / Issue #136.
#   - external_data_read + network_egress_cloud: notion_search /
#     notion_retrieve_page / notion_retrieve_block_children fetch the
#     user's pages and blocks from api.notion.com over the internet
#     (the gmail.py/youtube.py/todoist.py surface). network_egress_cloud
#     is what the AST audit actually requires (aiohttp/httpx call sites
#     -> NETWORK_EGRESS_ANY, call_site_capabilities.py:148-167).
#   - secrets_read is a DELIBERATE over-declaration -- the youtube.py /
#     gmail.py / todoist.py posture-B precedent (clone it, do NOT
#     contrast it). The AST audit maps secrets_read ONLY to
#     keyring.get_password/set_password
#     (call_site_capabilities.py:187-188) and is per-file/intraprocedural.
#     This plugin calls provider.current() -- never keyring.* directly
#     (the static token is read from os.environ in cerebral/main.py, an
#     unscanned file), so the audit will NOT auto-require secrets_read
#     here. We declare it anyway because the plugin's job is to surface
#     the user's pages behind an API credential, and handing that a
#     silent-class free pass is the wrong default (ADR-0005 threats
#     T1/T4). Do not "tidy this away" -- over-declaration is intentional
#     and audit-safe (_inspect only fails on *under*-declaration).
#   - external_data_write (Issue #136): notion_create_page mutates an
#     external account (it creates a page via POST /pages). This is the
#     correct *required* ask-class semantic class (ADR-0005 day-1 ACL,
#     line 34) -- NOT an over-declaration like secrets_read above. Like
#     external_data_read it is hand-declared: external_data_* is absent
#     from the AST capability map AND the bare-attr fallback
#     (call_site_capabilities.py:148-199 maps only fs/clipboard/network/
#     secrets/screen/device/code primitives), so the per-file AST audit
#     never auto-requires it -- a semantic capability declared because
#     the tool's effect IS the write, audit-safe.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_DEFAULT_PAGE_SIZE = 25
_DEFAULT_BLOCK_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 100
_MIN_PAGE_SIZE = 1

_FACTORY_NOT_WIRED_MSG = "Notion is not available -- token provider not wired"
_NO_TOKEN_MSG = "no Notion API token configured"
_MISSING_CREATE_ARG_MSG = "missing required arg(s) for notion_create_page: {}"
_MISSING_ID_MSG = "missing required arg(s) for {}: id"

# Text-bearing block types whose `rich_text` is concatenated into the
# shaped `text` field by `_shape_block`. All others get `text=""`.
_TEXT_BEARING_BLOCK_TYPES: frozenset[str] = frozenset({
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
    "code",
})


class TokenProvider(Protocol):
    """Per-active-profile API-token handle wired from main.py.

    Carries ONLY ``current()`` because Notion's Internal Integration
    Token is a STATIC user-rotated value (Notion workspace settings ->
    Connections -> Develop or build integrations -> Internal
    Integration). There is no OAuth refresh capability to describe, so
    the Protocol does not pretend one exists. This mirrors
    plugins/todoist.py's TokenProvider exactly -- a deliberate
    divergence from plugins/gmail.py / plugins/calendar.py (which carry
    both current() and refresh() for OAuth)."""

    def current(self) -> Optional[str]: ...


# Factory returns ``TokenProvider | None`` -- ``None`` when
# NOTION_API_TOKEN is unset. Set once at startup by cerebral/main.py
# via set_token_provider(), mirroring plugins/todoist.py /
# plugins/gmail.py. Tests pass a provider directly to the constructor
# (constructor injection wins).
TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_notion_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when
    ``NOTION_API_TOKEN`` is unset, a fresh handle otherwise. (The env
    var is system-wide; a future per-profile or settings-UI-backed
    slice would be additive.)
    """
    global _token_provider_factory
    _token_provider_factory = fn


class NotionAPIError(RuntimeError):
    """Any transport/HTTP failure of a Notion call. ``status`` is the
    HTTP status code when one is available, else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON
    or ``None`` for HTTP 204. (No Notion v1 endpoint in this slice
    returns 204, but the branch is carried verbatim from todoist.py for
    symmetry and as cheap insurance.)

    HTTP errors are mapped to NotionAPIError carrying the status code;
    transport/other errors carry status=None. Deps lazy-imported here
    so module import stays stdlib-only (learning #12).
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
                    if resp.status == 204:
                        return None
                    return await resp.json()
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise NotionAPIError(str(exc), status=exc.status) from exc
        except NotionAPIError:
            raise
        except Exception as exc:
            raise NotionAPIError(str(exc), status=None) from exc

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
                if resp.status_code == 204:
                    return None
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise NotionAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except NotionAPIError:
            raise
        except Exception as exc:
            raise NotionAPIError(str(exc), status=None) from exc

    raise NotionAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _extract_title(properties: Any, top_level_title: Any) -> str:
    """Pull a plain-text title from a Notion page/database response.

    Pages carry it under ``properties.title.title`` (a list of rich-text
    runs); databases carry it under a top-level ``title`` (same list
    shape). Returns the empty string if neither is well-formed.
    """
    if isinstance(properties, dict):
        title_prop = properties.get("title")
        if isinstance(title_prop, dict):
            runs = title_prop.get("title")
            if isinstance(runs, list):
                return "".join(
                    r.get("plain_text", "")
                    for r in runs
                    if isinstance(r, dict)
                )
    if isinstance(top_level_title, list):
        return "".join(
            r.get("plain_text", "")
            for r in top_level_title
            if isinstance(r, dict)
        )
    return ""


def _shape_object(obj: Any) -> dict:
    """Flatten one Notion page or database response row.

    Used by both ``notion_search`` (which can return pages or
    databases) and ``notion_retrieve_page`` (always a page). Field set:
    ``{id, object, title, url, created_time, last_edited_time,
    parent_type, parent_id, archived}``.

    Title extraction handles both page (``properties.title.title``) and
    database (top-level ``title``) shapes; missing/malformed yields
    ``""``. Parent shape ``{"type": "page_id", "page_id": "..."}`` ->
    ``parent_type="page_id"``, ``parent_id="..."``; workspace parent
    (``{"type": "workspace", "workspace": true}``) -> ``parent_id=""``.
    """
    if not isinstance(obj, dict):
        return {}
    parent = obj.get("parent")
    parent_type = ""
    parent_id = ""
    if isinstance(parent, dict):
        parent_type = parent.get("type", "") or ""
        if parent_type and parent_type != "workspace":
            parent_id = parent.get(parent_type, "") or ""
    return {
        "id": obj.get("id", "") or "",
        "object": obj.get("object", "") or "",
        "title": _extract_title(obj.get("properties"), obj.get("title")),
        "url": obj.get("url", "") or "",
        "created_time": obj.get("created_time", "") or "",
        "last_edited_time": obj.get("last_edited_time", "") or "",
        "parent_type": parent_type,
        "parent_id": parent_id,
        "archived": bool(obj.get("archived", False)),
    }


def _shape_block(b: Any) -> dict:
    """Flatten one Notion block response row.

    Field set: ``{id, type, text, has_children}``. ``text`` is the
    concatenation of ``rich_text[*].plain_text`` for the ten text-bearing
    block types in ``_TEXT_BEARING_BLOCK_TYPES`` (paragraph, heading_1-3,
    bulleted/numbered_list_item, to_do, quote, callout, code). All other
    block types (image, embed, divider, child_page, etc.) get
    ``text=""``. ``has_children`` echoed unchanged.
    """
    if not isinstance(b, dict):
        return {}
    block_type = b.get("type", "") or ""
    text = ""
    if block_type in _TEXT_BEARING_BLOCK_TYPES:
        typed_body = b.get(block_type)
        if isinstance(typed_body, dict):
            runs = typed_body.get("rich_text")
            if isinstance(runs, list):
                text = "".join(
                    r.get("plain_text", "")
                    for r in runs
                    if isinstance(r, dict)
                )
    return {
        "id": b.get("id", "") or "",
        "type": block_type,
        "text": text,
        "has_children": bool(b.get("has_children", False)),
    }


def _make_paragraph(text: str) -> dict:
    """Build one Notion paragraph block carrying a single plain-text
    rich-text run."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "text": {"content": text}},
            ],
        },
    }


def _build_create_body(parent_page_id: str, title: str,
                       content: str | None) -> dict:
    """Build the JSON body for ``POST /v1/pages`` for a page-parented
    create. Children blocks are derived from ``content`` by splitting on
    ``\\n\\n`` and wrapping each non-empty chunk in a paragraph block.
    Returns ``{"parent": ..., "properties": ...}`` (with ``"children"``
    added only when at least one non-empty chunk exists).
    """
    body: dict = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [
                    {"type": "text", "text": {"content": title}},
                ],
            },
        },
    }
    if isinstance(content, str) and content:
        chunks = [c for c in content.split("\n\n") if c]
        if chunks:
            body["children"] = [_make_paragraph(c) for c in chunks]
    return body


class NotionPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        # Constructor-injected provider wins over the module-level setter.
        # Tests pass directly; production leaves this None and main.py wires
        # the module-level _token_provider_factory at startup.
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        # Every bearer token ever held this call, so _scrub strips it from
        # any log line / ToolResult.
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="notion_search",
                description=(
                    "Search the user's Notion workspace for pages or "
                    "databases by query string. Returns id, object "
                    "(page|database), title, url, created_time, "
                    "last_edited_time, parent_type and parent_id for "
                    "each hit, plus a next_cursor for pagination. Empty "
                    "query lists everything accessible to the "
                    "integration."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Substring to search titles for. Empty "
                                "string lists every accessible object."
                            ),
                        },
                        "filter_type": {
                            "type": "string",
                            "description": (
                                "Narrow results to one object type: "
                                "'page' or 'database'. Any other value "
                                "is ignored."
                            ),
                        },
                        "page_size": {
                            "type": "integer",
                            "description": (
                                f"Results per page (default "
                                f"{_DEFAULT_PAGE_SIZE}, clamped to "
                                f"[{_MIN_PAGE_SIZE}, {_MAX_PAGE_SIZE}])."
                            ),
                        },
                        "start_cursor": {
                            "type": "string",
                            "description": (
                                "Pagination cursor from a prior "
                                "notion_search response's next_cursor."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="notion_retrieve_page",
                description=(
                    "Retrieve metadata for a single Notion page by id. "
                    "Returns the same shape as notion_search hits: id, "
                    "object, title, url, created_time, "
                    "last_edited_time, parent_type, parent_id, and "
                    "archived."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Notion page id (hyphenated or "
                                "dehyphenated UUID; either is accepted)."
                            ),
                        },
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="notion_retrieve_block_children",
                description=(
                    "Retrieve one page of children blocks under a "
                    "Notion page or block id, with plain-text extracted "
                    "from each text-bearing block (paragraph, "
                    "heading_1-3, bulleted/numbered list items, to-do, "
                    "quote, callout, code). Returns blocks: list of "
                    "{id, type, text, has_children} plus a next_cursor "
                    "for pagination. Non-text block types yield "
                    "text=\"\"; has_children=true means the LLM may "
                    "call this tool again with that block's id to walk "
                    "deeper."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Notion page or block id whose direct "
                                "children to fetch."
                            ),
                        },
                        "page_size": {
                            "type": "integer",
                            "description": (
                                f"Blocks per page (default "
                                f"{_DEFAULT_BLOCK_PAGE_SIZE}, clamped "
                                f"to [{_MIN_PAGE_SIZE}, "
                                f"{_MAX_PAGE_SIZE}])."
                            ),
                        },
                        "start_cursor": {
                            "type": "string",
                            "description": (
                                "Pagination cursor from a prior "
                                "notion_retrieve_block_children "
                                "response's next_cursor."
                            ),
                        },
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="notion_create_page",
                description=(
                    "Create a new page under an existing Notion page "
                    "(database-parented pages are not supported in this "
                    "slice). Optional content string is split on '\\n\\n' "
                    "into paragraph blocks for the initial content. "
                    "Returns id, object, title, url, created_time, "
                    "last_edited_time, parent_type, parent_id, archived."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "parent_page_id": {
                            "type": "string",
                            "description": (
                                "Id of the existing Notion page to nest "
                                "the new page under."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "Page title.",
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Optional initial body text. Split on "
                                "double newlines into separate "
                                "paragraph blocks; empty/whitespace "
                                "chunks dropped."
                            ),
                        },
                    },
                    "required": ["parent_page_id", "title"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "notion_search":
            return await self._search(args)
        if tool_name == "notion_retrieve_page":
            return await self._retrieve_page(args)
        if tool_name == "notion_retrieve_block_children":
            return await self._retrieve_block_children(args)
        if tool_name == "notion_create_page":
            return await self._create_page(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the
        todoist.py / gmail.py / calendar.py posture)."""
        factory = self._token_factory()
        if self._provider is not None:
            return self._provider, None
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        provider = factory()
        if provider is None:
            return None, _NO_TOKEN_MSG
        return provider, None

    @staticmethod
    def _token_factory() -> Optional[TokenProviderFactory]:
        return _token_provider_factory

    def _scrub(self, text: str) -> str:
        """Strip every bearer token seen this call from text bound for
        a log or ToolResult."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider) -> str:
        """Get the static API token from the provider. Records it for
        scrubbing. No refresh path -- Notion's Internal Integration
        Token is static."""
        token = provider.current() or ""
        if token:
            self._seen_tokens.add(token)
        return token

    async def _request(self, method: str, endpoint: str, token: str, *,
                       params: dict | None = None,
                       json: dict | None = None) -> Any:
        return await self._fetch(
            method,
            f"{_BASE}/{endpoint}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": _NOTION_VERSION,
            },
            params=params,
            json=json,
        )

    def _clamp_page_size(self, raw: Any, *, default: int) -> int:
        try:
            value = int(raw) if raw is not None else default
        except (TypeError, ValueError):
            value = default
        return max(_MIN_PAGE_SIZE, min(_MAX_PAGE_SIZE, value))

    async def _search(self, args: dict) -> ToolResult:
        body: dict = {}
        query = args.get("query")
        if isinstance(query, str):
            body["query"] = query
        filter_type = args.get("filter_type")
        if isinstance(filter_type, str) and filter_type in ("page", "database"):
            body["filter"] = {"value": filter_type, "property": "object"}
        body["page_size"] = self._clamp_page_size(
            args.get("page_size"), default=_DEFAULT_PAGE_SIZE,
        )
        start_cursor = args.get("start_cursor")
        if isinstance(start_cursor, str) and start_cursor:
            body["start_cursor"] = start_cursor

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request("POST", "search", token, json=body)
        except Exception as exc:
            logger.error(
                "[notion] notion_search failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Notion request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Notion search response", is_error=True,
            )

        results = resp.get("results") or []
        if not isinstance(results, list):
            results = []
        shaped = [_shape_object(r) for r in results]
        return ToolResult(content=json.dumps({
            "results": shaped,
            "next_cursor": resp.get("next_cursor"),
        }))

    @staticmethod
    def _require_id(args: dict, tool_name: str) -> tuple[str | None, ToolResult | None]:
        """Return (id, None) on success or (None, error_ToolResult)."""
        obj_id = args.get("id")
        if not isinstance(obj_id, str) or not obj_id:
            return None, ToolResult(
                content=_MISSING_ID_MSG.format(tool_name),
                is_error=True,
            )
        return obj_id, None

    async def _retrieve_page(self, args: dict) -> ToolResult:
        page_id, err_result = self._require_id(args, "notion_retrieve_page")
        if err_result is not None:
            return err_result

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request("GET", f"pages/{page_id}", token)
        except Exception as exc:
            logger.error(
                "[notion] notion_retrieve_page failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Notion request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Notion retrieve_page response",
                is_error=True,
            )
        return ToolResult(content=json.dumps(_shape_object(resp)))

    async def _retrieve_block_children(self, args: dict) -> ToolResult:
        block_id, err_result = self._require_id(
            args, "notion_retrieve_block_children",
        )
        if err_result is not None:
            return err_result

        params: dict = {
            "page_size": self._clamp_page_size(
                args.get("page_size"), default=_DEFAULT_BLOCK_PAGE_SIZE,
            ),
        }
        start_cursor = args.get("start_cursor")
        if isinstance(start_cursor, str) and start_cursor:
            params["start_cursor"] = start_cursor

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request(
                "GET", f"blocks/{block_id}/children", token, params=params,
            )
        except Exception as exc:
            logger.error(
                "[notion] notion_retrieve_block_children failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Notion request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Notion block-children response",
                is_error=True,
            )

        results = resp.get("results") or []
        if not isinstance(results, list):
            results = []
        shaped = [_shape_block(b) for b in results]
        return ToolResult(content=json.dumps({
            "blocks": shaped,
            "next_cursor": resp.get("next_cursor"),
        }))

    async def _create_page(self, args: dict) -> ToolResult:
        parent_page_id = args.get("parent_page_id")
        title = args.get("title")
        missing: list[str] = []
        if not isinstance(parent_page_id, str) or not parent_page_id:
            missing.append("parent_page_id")
        if not isinstance(title, str) or not title:
            missing.append("title")
        if missing:
            return ToolResult(
                content=_MISSING_CREATE_ARG_MSG.format(", ".join(missing)),
                is_error=True,
            )

        content = args.get("content")
        body = _build_create_body(
            parent_page_id, title,
            content if isinstance(content, str) else None,
        )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request("POST", "pages", token, json=body)
        except Exception as exc:
            logger.error(
                "[notion] notion_create_page failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Notion request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Notion create_page response",
                is_error=True,
            )
        return ToolResult(content=json.dumps(_shape_object(resp)))


def create(fetch_fn: FetchFn | None = None) -> NotionPlugin:
    return NotionPlugin(fetch_fn=fetch_fn)
