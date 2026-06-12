"""
Google Drive MCP plugin -- Issue #228, ADR-0005.

Five tools, hitting the **real Google Drive API v3**, authorized by the
active profile's OAuth **access token** read from the #112 credential store
(refreshed on demand via #113):

  - ``drive_list_files``    -- Drive API ``files.list`` with optional query
                               (read-only).
  - ``drive_upload_file``   -- Drive API ``files.create`` (multipart upload,
                               write, ask-class ``external_data_write``).
  - ``drive_download_file`` -- Drive API ``files.get`` metadata + download URL
                               (read-only).
  - ``drive_create_folder`` -- Drive API ``files.create`` (folder MIME type,
                               write, ask-class ``external_data_write``).
  - ``drive_share_file``    -- Drive API ``permissions.create`` (write,
                               ask-class ``external_data_write``).

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/gmail.py`` / ``plugins/calendar.py`` / ``plugins/google_tasks.py``
precedent. Tests bypass it by passing a stub provider + stub ``fetch_fn`` to
the constructor: no real network, OAuth, keyring or browser in the suite.

**No live-verify in this slice.** Live verification is batched into slice
V.1 (#239) so the autonomous loop is never blocked on browser OAuth consent.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_drive"

# ADR-0005 / Issue #228.
#   - external_data_read + network_egress_cloud: drive_list_files and
#     drive_download_file fetch data from www.googleapis.com.
#   - secrets_read is a DELIBERATE over-declaration -- the gmail.py /
#     calendar.py posture-B precedent (clone it, do NOT contrast it).
#   - external_data_write (drive_upload_file, drive_create_folder,
#     drive_share_file): these tools mutate the active profile's Drive.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_DEFAULT_LIMIT = 10
_FOLDER_MIME = "application/vnd.google-apps.folder"

_FACTORY_NOT_WIRED_MSG = "Google Drive is not available -- token provider not wired"
_NO_ACCOUNT_MSG = "no Google account connected"
_MISSING_UPLOAD_ARGS_MSG = "missing required arg(s) for drive_upload_file: {}"
_MISSING_DOWNLOAD_ARGS_MSG = "missing required arg(s) for drive_download_file: {}"
_MISSING_FOLDER_ARGS_MSG = "missing required arg(s) for drive_create_folder: {}"
_MISSING_SHARE_ARGS_MSG = "missing required arg(s) for drive_share_file: {}"


class TokenProvider(Protocol):
    """Per-active-profile bearer-token handle wired from main.py."""

    def current(self) -> Optional[str]: ...
    def refresh(self) -> str: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_google_drive_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin."""
    global _token_provider_factory
    _token_provider_factory = fn


class DriveAPIError(RuntimeError):
    """Any transport/HTTP failure of a Drive call. ``status`` is the HTTP
    status code when available (so the 401 -> refresh -> retry path can
    branch on it), else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None,
                         data: bytes | None = None,
                         content_type: str | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    ``data``/``content_type`` are used for multipart upload; ``json`` is
    used for all other calls. HTTP errors map to DriveAPIError.
    """
    _headers = dict(headers or {})
    if content_type is not None:
        _headers["Content-Type"] = content_type

    try:
        import aiohttp  # type: ignore
    except ImportError:
        aiohttp = None  # type: ignore
    if aiohttp is not None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url,
                    headers=_headers or None,
                    params=params,
                    json=json if data is None else None,
                    data=data,
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise DriveAPIError(str(exc), status=exc.status) from exc
        except DriveAPIError:
            raise
        except Exception as exc:
            raise DriveAPIError(str(exc), status=None) from exc

    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None  # type: ignore
    if httpx is not None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method, url,
                    headers=_headers or None,
                    params=params,
                    json=json if data is None else None,
                    content=data,
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise DriveAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except DriveAPIError:
            raise
        except Exception as exc:
            raise DriveAPIError(str(exc), status=None) from exc

    raise DriveAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _shape_file(f: Any) -> dict:
    """Flatten one files.list response row."""
    if not isinstance(f, dict):
        return {}
    return {
        "id": f.get("id", ""),
        "name": f.get("name", ""),
        "mimeType": f.get("mimeType", ""),
        "size": f.get("size", ""),
        "webViewLink": f.get("webViewLink", ""),
    }


def _build_multipart(metadata: dict, content: str,
                     mime_type: str) -> tuple[bytes, str]:
    """Build a multipart/related body for the Drive upload API.

    Returns ``(body_bytes, content_type_header_value)``.
    """
    boundary = uuid.uuid4().hex
    meta_json = json.dumps(metadata).encode()
    content_bytes = content.encode()
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode() + meta_json + (
        f"\r\n--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode() + content_bytes + (
        f"\r\n--{boundary}--"
    ).encode()
    return body, f"multipart/related; boundary={boundary}"


class GoogleDrivePlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="drive_list_files",
                description=(
                    "List or search files in the active profile's Google Drive. "
                    "Supports optional query string (Drive query syntax)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Drive query string, e.g. "
                                "\"name contains 'report'\". "
                                "Omit to list all files."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum files to return "
                                f"(default {_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="drive_upload_file",
                description=(
                    "Upload a text file to Google Drive. Returns the new "
                    "file ID and name."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "File name including extension.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Text content of the file.",
                        },
                        "mime_type": {
                            "type": "string",
                            "description": (
                                "MIME type (default \"text/plain\")."
                            ),
                        },
                        "parent_id": {
                            "type": "string",
                            "description": "Parent folder ID (optional).",
                        },
                    },
                    "required": ["name", "content"],
                },
            ),
            Tool(
                name="drive_download_file",
                description=(
                    "Download a file from Google Drive. Returns metadata and "
                    "download links. For Google Workspace files (Docs, Sheets "
                    "etc.) the webViewLink opens the file in the browser."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "The Drive file ID.",
                        },
                    },
                    "required": ["file_id"],
                },
            ),
            Tool(
                name="drive_create_folder",
                description=(
                    "Create a new folder in Google Drive."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Folder name.",
                        },
                        "parent_id": {
                            "type": "string",
                            "description": "Parent folder ID (optional).",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="drive_share_file",
                description=(
                    "Share a Drive file with a user by email. Roles: "
                    "reader, commenter, writer, fileOrganizer, organizer, owner."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "The Drive file ID.",
                        },
                        "email": {
                            "type": "string",
                            "description": "Email address of the recipient.",
                        },
                        "role": {
                            "type": "string",
                            "description": (
                                "Permission role (default \"reader\")."
                            ),
                        },
                    },
                    "required": ["file_id", "email"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "drive_list_files":
            return await self._list_files(args)
        if tool_name == "drive_upload_file":
            return await self._upload_file(args)
        if tool_name == "drive_download_file":
            return await self._download_file(args)
        if tool_name == "drive_create_folder":
            return await self._create_folder(args)
        if tool_name == "drive_share_file":
            return await self._share_file(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
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
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider, *, force: bool) -> str:
        token = None if force else provider.current()
        if not token:
            token = provider.refresh()
        if token:
            self._seen_tokens.add(token)
        return token or ""

    async def _get_files(self, token: str,
                         params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/files",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _post_multipart(self, token: str, params: dict,
                              body: bytes, ct: str) -> Any:
        return await self._fetch(
            "POST",
            f"{_UPLOAD_BASE}/files",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            data=body,
            content_type=ct,
        )

    async def _get_file_meta(self, token: str, file_id: str,
                             params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _post_file(self, token: str, body: dict) -> Any:
        return await self._fetch(
            "POST",
            f"{_BASE}/files",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _post_permission(self, token: str, file_id: str,
                               body: dict) -> Any:
        return await self._fetch(
            "POST",
            f"{_BASE}/files/{file_id}/permissions",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _list_files(self, args: dict) -> ToolResult:
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params: dict = {
            "pageSize": max_results,
            "fields": "files(id,name,mimeType,size,webViewLink)",
        }
        query = args.get("query")
        if isinstance(query, str) and query:
            params["q"] = query

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                listing = await self._get_files(token, params=params)
            except DriveAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                listing = await self._get_files(token, params=params)
        except Exception as exc:
            logger.error(
                "[google_drive] drive_list_files failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Drive list failed: {exc}"),
                is_error=True,
            )

        if not isinstance(listing, dict):
            return ToolResult(
                content="unexpected Drive list response", is_error=True
            )
        items = [
            f for f in (listing.get("files") or [])
            if isinstance(f, dict)
        ][:max_results]
        return ToolResult(content=json.dumps({"files": [
            _shape_file(f) for f in items
        ]}))

    async def _upload_file(self, args: dict) -> ToolResult:
        name = args.get("name")
        content = args.get("content")
        missing = [
            k for k in ("name", "content")
            if not args.get(k) or not isinstance(args.get(k), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_UPLOAD_ARGS_MSG.format(", ".join(missing)),
                is_error=True,
            )

        mime_type = args.get("mime_type") or "text/plain"
        if not isinstance(mime_type, str):
            mime_type = "text/plain"
        parent_id = args.get("parent_id")

        metadata: dict = {"name": name, "mimeType": mime_type}
        if isinstance(parent_id, str) and parent_id:
            metadata["parents"] = [parent_id]

        body, ct = _build_multipart(metadata, content, mime_type)
        params = {"uploadType": "multipart"}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._post_multipart(token, params, body, ct)
            except DriveAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._post_multipart(token, params, body, ct)
        except Exception as exc:
            logger.error(
                "[google_drive] drive_upload_file failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Drive upload failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Drive upload response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "name": resp.get("name", ""),
            "status": "uploaded",
        }))

    async def _download_file(self, args: dict) -> ToolResult:
        file_id = args.get("file_id")
        if not file_id or not isinstance(file_id, str):
            return ToolResult(
                content=_MISSING_DOWNLOAD_ARGS_MSG.format("file_id"),
                is_error=True,
            )

        params = {"fields": "id,name,mimeType,size,webViewLink,webContentLink"}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                meta = await self._get_file_meta(token, file_id, params=params)
            except DriveAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                meta = await self._get_file_meta(token, file_id, params=params)
        except Exception as exc:
            logger.error(
                "[google_drive] drive_download_file failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Drive download failed: {exc}"),
                is_error=True,
            )

        if not isinstance(meta, dict):
            return ToolResult(
                content="unexpected Drive file metadata response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": meta.get("id", ""),
            "name": meta.get("name", ""),
            "mimeType": meta.get("mimeType", ""),
            "size": meta.get("size", ""),
            "webViewLink": meta.get("webViewLink", ""),
            "webContentLink": meta.get("webContentLink", ""),
        }))

    async def _create_folder(self, args: dict) -> ToolResult:
        name = args.get("name")
        if not name or not isinstance(name, str):
            return ToolResult(
                content=_MISSING_FOLDER_ARGS_MSG.format("name"),
                is_error=True,
            )

        parent_id = args.get("parent_id")
        body: dict = {"name": name, "mimeType": _FOLDER_MIME}
        if isinstance(parent_id, str) and parent_id:
            body["parents"] = [parent_id]

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._post_file(token, body)
            except DriveAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._post_file(token, body)
        except Exception as exc:
            logger.error(
                "[google_drive] drive_create_folder failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Drive create folder failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Drive create folder response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "name": resp.get("name", ""),
            "status": "created",
        }))

    async def _share_file(self, args: dict) -> ToolResult:
        file_id = args.get("file_id")
        email = args.get("email")
        missing = [
            k for k in ("file_id", "email")
            if not args.get(k) or not isinstance(args.get(k), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_SHARE_ARGS_MSG.format(", ".join(missing)),
                is_error=True,
            )

        role = args.get("role") or "reader"
        if not isinstance(role, str):
            role = "reader"
        body = {"type": "user", "role": role, "emailAddress": email}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._post_permission(token, file_id, body)
            except DriveAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._post_permission(token, file_id, body)
        except Exception as exc:
            logger.error(
                "[google_drive] drive_share_file failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Drive share failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Drive share response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "role": resp.get("role", ""),
            "status": "shared",
        }))


def create(fetch_fn: FetchFn | None = None) -> GoogleDrivePlugin:
    """Zero-arg-by-default factory the orchestrator discovers.

    ``fetch_fn`` is for tests; production leaves it None. The token
    provider is wired separately via ``set_token_provider`` from
    ``cerebral/main.py``."""
    return GoogleDrivePlugin(fetch_fn=fetch_fn)
