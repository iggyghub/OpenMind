"""
Google Sheets MCP plugin -- Issue #225, ADR-0005.

Five tools, hitting the **real Google Sheets API** (and Drive API for
listing), authorized by the active profile's OAuth **access token** read
from the #112 credential store (refreshed on demand via #113):

  - ``sheets_list``         -- Drive API ``files.list`` filtered to Sheets
                               MIME type. Read-only.
  - ``sheets_read_range``   -- Sheets API ``values.get``. Read-only.
  - ``sheets_write_range``  -- Sheets API ``values.update`` (PUT). Write
                               (ask-class ``external_data_write``).
  - ``sheets_append_row``   -- Sheets API ``values.append`` (POST). Write
                               (ask-class ``external_data_write``).
  - ``sheets_create``       -- Sheets API ``spreadsheets.create`` (POST).
                               Write (ask-class ``external_data_write``).

This supersedes the legacy n8n-bridge Sheets tools in
``plugins/google_workspace.py``, but that file is left intact -- removal is
batched in cleanup slice B.8 (#231).

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/gmail.py`` / ``plugins/calendar.py`` / ``plugins/google_docs.py``
precedent. Tests bypass it by passing a stub provider + stub ``fetch_fn`` to
the constructor: no real network, OAuth, keyring or browser in the suite.

**No live-verify in this slice.** Live verification is batched into slice
V.1 (#239) so the autonomous loop is never blocked on browser OAuth consent.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_sheets"

# ADR-0005 / Issue #225.
#   - external_data_read + network_egress_cloud: sheets_list / sheets_read_range
#     fetch data from googleapis.com over the internet (the gmail.py /
#     calendar.py surface).
#   - secrets_read is a DELIBERATE over-declaration -- the gmail.py /
#     calendar.py posture-B precedent (clone it, do NOT contrast it). The
#     AST audit maps secrets_read ONLY to keyring.get_password/set_password
#     and is per-file/intraprocedural. This plugin calls
#     provider.current()/refresh() -- never keyring.* directly (the keyring
#     read lives in cerebral/db/credentials.py, an unscanned file), so the
#     audit will NOT auto-require secrets_read here. We declare it anyway
#     because the plugin's job is to surface the user's spreadsheets behind
#     an OAuth credential (ADR-0005 threats T1/T4). Do not "tidy this away"
#     -- over-declaration is intentional and audit-safe.
#   - external_data_write (sheets_write_range, sheets_append_row,
#     sheets_create): these tools mutate an external account.
#     Hand-declared: external_data_* is absent from the AST capability map
#     AND the bare-attr fallback.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
_DEFAULT_LIMIT = 10

_FACTORY_NOT_WIRED_MSG = (
    "Google Sheets is not available -- token provider not wired"
)
_NO_ACCOUNT_MSG = "no Google account connected"
_MISSING_READ_RANGE_ARGS_MSG = "missing required arg(s) for sheets_read_range: {}"
_MISSING_WRITE_RANGE_ARGS_MSG = "missing required arg(s) for sheets_write_range: {}"
_MISSING_APPEND_ROW_ARGS_MSG = "missing required arg(s) for sheets_append_row: {}"
_MISSING_CREATE_ARG_MSG = "missing required arg(s) for sheets_create: {}"


class TokenProvider(Protocol):
    """Per-active-profile bearer-token handle wired from main.py.

    ``current()`` returns the stored access token (no network) or ``None``;
    ``refresh()`` exchanges the stored refresh token for a fresh access
    token via #113 (the 401 path) and returns it.
    """

    def current(self) -> Optional[str]: ...
    def refresh(self) -> str: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_google_sheets_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin."""
    global _token_provider_factory
    _token_provider_factory = fn


class SheetsAPIError(RuntimeError):
    """Any transport/HTTP failure of a Sheets or Drive call. ``status`` is
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

    HTTP errors are mapped to SheetsAPIError carrying the status code so the
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
            raise SheetsAPIError(str(exc), status=exc.status) from exc
        except SheetsAPIError:
            raise
        except Exception as exc:
            raise SheetsAPIError(str(exc), status=None) from exc

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
            raise SheetsAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except SheetsAPIError:
            raise
        except Exception as exc:
            raise SheetsAPIError(str(exc), status=None) from exc

    raise SheetsAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


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


class GoogleSheetsPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="sheets_list",
                description=(
                    "List recent Google Sheets spreadsheets from the active "
                    "profile's Google Drive, ordered by last modified time. "
                    "Returns spreadsheet id, name, modified time, and web "
                    "view link. Read-only."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum spreadsheets to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="sheets_read_range",
                description=(
                    "Read a cell range from a Google Sheets spreadsheet via "
                    "the real Sheets API. Returns the spreadsheet id, range, "
                    "and values as a 2-D array. Read-only."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_id": {
                            "type": "string",
                            "description": "Google Sheets spreadsheet ID.",
                        },
                        "range": {
                            "type": "string",
                            "description": (
                                "A1 notation range, e.g. 'Sheet1!A1:C10'."
                            ),
                        },
                    },
                    "required": ["spreadsheet_id", "range"],
                },
            ),
            Tool(
                name="sheets_write_range",
                description=(
                    "Write (overwrite) a cell range in a Google Sheets "
                    "spreadsheet via the Sheets API values.update endpoint. "
                    "Returns the updated spreadsheet id, range, and updated "
                    "cell count."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,
                schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_id": {
                            "type": "string",
                            "description": "Google Sheets spreadsheet ID.",
                        },
                        "range": {
                            "type": "string",
                            "description": (
                                "A1 notation range, e.g. 'Sheet1!A1:C2'."
                            ),
                        },
                        "values": {
                            "type": "array",
                            "description": (
                                "2-D array of values to write, row-major."
                            ),
                            "items": {"type": "array"},
                        },
                    },
                    "required": ["spreadsheet_id", "range", "values"],
                },
            ),
            Tool(
                name="sheets_append_row",
                description=(
                    "Append a row of values to a Google Sheets spreadsheet "
                    "via the Sheets API values.append endpoint. Returns the "
                    "updated spreadsheet id, table range, and updated row "
                    "count."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,
                schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_id": {
                            "type": "string",
                            "description": "Google Sheets spreadsheet ID.",
                        },
                        "range": {
                            "type": "string",
                            "description": (
                                "Sheet or range to append to, e.g. 'Sheet1'."
                            ),
                        },
                        "values": {
                            "type": "array",
                            "description": "1-D row of values to append.",
                        },
                    },
                    "required": ["spreadsheet_id", "range", "values"],
                },
            ),
            Tool(
                name="sheets_create",
                description=(
                    "Create a new Google Sheets spreadsheet in the active "
                    "profile's Google Drive via the real Sheets API. "
                    "Returns the new spreadsheet id and title."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Title of the new spreadsheet.",
                        },
                    },
                    "required": ["title"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "sheets_list":
            return await self._list(args)
        if tool_name == "sheets_read_range":
            return await self._read_range(args)
        if tool_name == "sheets_write_range":
            return await self._write_range(args)
        if tool_name == "sheets_append_row":
            return await self._append_row(args)
        if tool_name == "sheets_create":
            return await self._create(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None."""
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

    async def _put(self, url: str, token: str, body: dict,
                   params: dict | None = None) -> Any:
        return await self._fetch(
            "PUT", url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json=body,
        )

    # ── sheets_list ────────────────────────────────────────────────────

    async def _run_list(self, token: str, max_results: int) -> list[dict]:
        resp = await self._get(
            _DRIVE_FILES, token,
            params={
                "q": f"mimeType='{_SHEETS_MIME}'",
                "orderBy": "modifiedTime desc",
                "pageSize": max_results,
                "fields": "files(id,name,modifiedTime,webViewLink)",
            },
        )
        if not isinstance(resp, dict):
            raise SheetsAPIError("unexpected Drive list response", status=None)
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
                items = await self._run_list(token, max_results)
            except SheetsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                items = await self._run_list(token, max_results)
        except Exception as exc:
            logger.error(
                "[google_sheets] sheets_list failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Sheets list failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({"spreadsheets": items}))

    # ── sheets_read_range ──────────────────────────────────────────────

    async def _run_read_range(self, token: str, spreadsheet_id: str,
                              range_: str) -> dict:
        url = f"{_SHEETS_BASE}/{spreadsheet_id}/values/{range_}"
        resp = await self._get(url, token)
        if not isinstance(resp, dict):
            raise SheetsAPIError(
                "unexpected Sheets values.get response", status=None
            )
        return resp

    async def _read_range(self, args: dict) -> ToolResult:
        missing = [
            name for name in ("spreadsheet_id", "range")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_READ_RANGE_ARGS_MSG.format(
                    ", ".join(missing)
                ),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        spreadsheet_id = args["spreadsheet_id"]
        range_ = args["range"]

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._run_read_range(token, spreadsheet_id, range_)
            except SheetsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._run_read_range(token, spreadsheet_id, range_)
        except Exception as exc:
            logger.error(
                "[google_sheets] sheets_read_range failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Sheets read_range failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({
            "spreadsheet_id": spreadsheet_id,
            "range": resp.get("range", range_),
            "values": resp.get("values", []),
        }))

    # ── sheets_write_range ─────────────────────────────────────────────

    async def _run_write_range(self, token: str, spreadsheet_id: str,
                               range_: str, values: list) -> dict:
        url = f"{_SHEETS_BASE}/{spreadsheet_id}/values/{range_}"
        resp = await self._put(
            url, token,
            {"range": range_, "majorDimension": "ROWS", "values": values},
            params={"valueInputOption": "RAW"},
        )
        if not isinstance(resp, dict):
            raise SheetsAPIError(
                "unexpected Sheets values.update response", status=None
            )
        return resp

    async def _write_range(self, args: dict) -> ToolResult:
        missing = [
            name for name in ("spreadsheet_id", "range")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if "values" not in args or not isinstance(args.get("values"), list):
            missing.append("values")
        if missing:
            return ToolResult(
                content=_MISSING_WRITE_RANGE_ARGS_MSG.format(
                    ", ".join(missing)
                ),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        spreadsheet_id = args["spreadsheet_id"]
        range_ = args["range"]
        values = args["values"]

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._run_write_range(
                    token, spreadsheet_id, range_, values
                )
            except SheetsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._run_write_range(
                    token, spreadsheet_id, range_, values
                )
        except Exception as exc:
            logger.error(
                "[google_sheets] sheets_write_range failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Sheets write_range failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({
            "spreadsheet_id": resp.get("spreadsheetId", spreadsheet_id),
            "updated_range": resp.get("updatedRange", range_),
            "updated_cells": resp.get("updatedCells", 0),
        }))

    # ── sheets_append_row ──────────────────────────────────────────────

    async def _run_append_row(self, token: str, spreadsheet_id: str,
                              range_: str, values: list) -> dict:
        url = (
            f"{_SHEETS_BASE}/{spreadsheet_id}/values/{range_}:append"
        )
        resp = await self._post(
            url, token,
            {"majorDimension": "ROWS", "values": [values]},
        )
        if not isinstance(resp, dict):
            raise SheetsAPIError(
                "unexpected Sheets values.append response", status=None
            )
        return resp

    async def _append_row(self, args: dict) -> ToolResult:
        missing = [
            name for name in ("spreadsheet_id", "range")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if "values" not in args or not isinstance(args.get("values"), list):
            missing.append("values")
        if missing:
            return ToolResult(
                content=_MISSING_APPEND_ROW_ARGS_MSG.format(
                    ", ".join(missing)
                ),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        spreadsheet_id = args["spreadsheet_id"]
        range_ = args["range"]
        values = args["values"]

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._run_append_row(
                    token, spreadsheet_id, range_, values
                )
            except SheetsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._run_append_row(
                    token, spreadsheet_id, range_, values
                )
        except Exception as exc:
            logger.error(
                "[google_sheets] sheets_append_row failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Sheets append_row failed: {exc}"),
                is_error=True,
            )

        updates = resp.get("updates") or {}
        return ToolResult(content=json.dumps({
            "spreadsheet_id": resp.get("spreadsheetId", spreadsheet_id),
            "table_range": updates.get("updatedRange", ""),
            "updated_rows": updates.get("updatedRows", 0),
        }))

    # ── sheets_create ──────────────────────────────────────────────────

    async def _run_create(self, token: str, title: str) -> dict:
        resp = await self._post(
            _SHEETS_BASE, token,
            {"properties": {"title": title}},
        )
        if not isinstance(resp, dict):
            raise SheetsAPIError(
                "unexpected Sheets create response", status=None
            )
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
            except SheetsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._run_create(token, args["title"])
        except Exception as exc:
            logger.error(
                "[google_sheets] sheets_create failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Sheets create failed: {exc}"),
                is_error=True,
            )

        props = resp.get("properties") or {}
        return ToolResult(content=json.dumps({
            "spreadsheet_id": resp.get("spreadsheetId", ""),
            "title": props.get("title", args["title"]),
        }))


def create(fetch_fn: FetchFn | None = None) -> GoogleSheetsPlugin:
    return GoogleSheetsPlugin(fetch_fn=fetch_fn)
