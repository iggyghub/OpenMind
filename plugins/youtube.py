"""
YouTube MCP plugin — Issue #109 / Issue #148.

Tools: youtube_search, youtube_video, youtube_channel.

Stateless, read-only over the YouTube Data API v3 public-read endpoints —
a single static API key (the ``?key=`` query parameter), no OAuth, no token
refresh, no SQLite, no state:
  - Search:  https://www.googleapis.com/youtube/v3/search
  - Video:   https://www.googleapis.com/youtube/v3/videos
  - Channel: https://www.googleapis.com/youtube/v3/channels

Clones the plugins/wikipedia.py transport posture (public JSON API, injectable
async fetch_fn, no auth header — the key is a query param assembled by the
tool methods, not a transport concern).

Issue #148 — the API key reaches the plugin via a module-level
``set_token_provider`` seam mirroring plugins/todoist.py / plugins/notion.py
/ plugins/toggl.py / plugins/clockify.py. The TokenProvider Protocol carries
only ``current()`` (static key, no refresh). Tests inject a stub provider
via the constructor + a stub fetch_fn so they never read the real
environment or hit the network. Before #148 this plugin read the
``YOUTUBE_API_KEY`` env var directly in ``__init__`` (one-shot at
construction); after #148 the key comes from per-profile keyring (via
#112's CredentialStore) with the env var as a fallback, and is
re-resolved on every tool call so a freshly-set key picks up without a
Cerebral restart.
"""
import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "youtube"

# ADR-0005 / Issue #109.
#   - external_data_read + network_egress_cloud: youtube_* fetch public data
#     from googleapis.com over the internet (the wikipedia.py/reddit.py
#     surface). network_egress_cloud is what the AST audit actually requires
#     (httpx/aiohttp call sites → NETWORK_EGRESS_ANY).
#   - secrets_read is a DELIBERATE over-declaration. The AST audit maps
#     secrets_read only to keyring.* (call_site_capabilities.py:187-188); an
#     os.environ read is mapped to nothing, so the audit passes without it.
#     We declare it anyway because the plugin reads credential material (the
#     API key) and handing that a silent-class free pass is the wrong default
#     for the first directly-keyed plugin (ADR-0005 threats T1/T4). Do not
#     "tidy this away" — over-declaration is intentional and audit-safe.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "network_egress_cloud",
})

_BASE = "https://www.googleapis.com/youtube/v3"
_DEFAULT_LIMIT = 10

_FACTORY_NOT_WIRED_MSG = "YouTube is not available — token provider not wired"
_NO_TOKEN_MSG = "YOUTUBE_API_KEY is not configured"

FetchFn = Callable[..., Awaitable[Any]]


class TokenProvider(Protocol):
    """Per-active-profile API-key handle wired from main.py.

    Carries ONLY ``current()`` because YouTube's API key is a STATIC
    user-rotated value (Google Cloud Console → Credentials → API key).
    There is no OAuth refresh capability to describe, so the Protocol
    does not pretend one exists. Mirrors plugins/todoist.py /
    plugins/notion.py / plugins/toggl.py / plugins/clockify.py."""

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_youtube_token_provider() — called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when no
    key is configured (neither per-profile keyring nor YOUTUBE_API_KEY
    env), a fresh handle otherwise."""
    global _token_provider_factory
    _token_provider_factory = fn


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp → httpx fallback. Returns parsed JSON.

    No auth header — the YouTube key is a ``?key=`` query parameter carried
    in ``params`` by the caller. Same no-header form as
    plugins/wikipedia.py; deps lazy-imported here so module import stays
    stdlib-only.
    """
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers,
                                        params=params, json=json) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers,
                                        params=params, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


def _shape_search(payload: Any) -> list[dict]:
    """Flatten a search.list response.

    search.list `id` is an OBJECT {"kind":..., "videoId":...} — not the
    plain-string `id` of videos.list/channels.list.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        snippet = item.get("snippet", {}) or {}
        ident = item.get("id", {}) or {}
        out.append({
            "video_id": ident.get("videoId", "") if isinstance(ident, dict) else "",
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
            "description": snippet.get("description", ""),
        })
    return out


def _shape_video(item: dict) -> dict:
    """Flatten one videos.list item. `id` is a plain string here."""
    snippet = item.get("snippet", {}) or {}
    stats = item.get("statistics", {}) or {}
    return {
        "video_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "published_at": snippet.get("publishedAt", ""),
        "view_count": stats.get("viewCount", ""),
        "like_count": stats.get("likeCount", ""),
        "comment_count": stats.get("commentCount", ""),
        "description": snippet.get("description", ""),
    }


def _shape_channel(item: dict) -> dict:
    """Flatten one channels.list item. `id` is a plain string here."""
    snippet = item.get("snippet", {}) or {}
    stats = item.get("statistics", {}) or {}
    return {
        "channel_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "subscriber_count": stats.get("subscriberCount", ""),
        "video_count": stats.get("videoCount", ""),
        "view_count": stats.get("viewCount", ""),
    }


class YouTubePlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        # Constructor-injected provider wins over the module-level setter.
        # Tests pass directly; production leaves this None and main.py
        # wires the module-level _token_provider_factory at startup
        # (Issue #148 — was os.environ.get("YOUTUBE_API_KEY") in __init__
        # before the TokenProvider migration).
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        # Every key ever held this call, so _scrub strips it from any log
        # line / ToolResult (the key is a ?key= query param; aiohttp/httpx
        # status errors embed the full request URL in their message).
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="youtube_search",
                description=(
                    "Search YouTube for videos by query. Returns video id, "
                    "title, channel, publish date and description."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search term."},
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum videos to return (default {_DEFAULT_LIMIT}).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="youtube_video",
                description=(
                    "Fetch metadata and statistics for a single video by id."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "video_id": {"type": "string", "description": "YouTube video id."},
                    },
                    "required": ["video_id"],
                },
            ),
            Tool(
                name="youtube_channel",
                description=(
                    "Fetch metadata and statistics for a channel by id or by "
                    "@handle. Provide exactly one of channel_id or handle."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string", "description": "YouTube channel id."},
                        "handle": {
                            "type": "string",
                            "description": "Channel @handle (with or without the leading @).",
                        },
                    },
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "youtube_search":
            return await self._search(args)
        if tool_name == "youtube_video":
            return await self._video(args)
        if tool_name == "youtube_channel":
            return await self._channel(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        Mirrors plugins/todoist.py / plugins/notion.py / plugins/toggl.py /
        plugins/clockify.py."""
        if self._provider is not None:
            return self._provider, None
        factory = self._token_factory()
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
        """Strip every API key seen this call from text bound for a log or
        ToolResult.

        The key is a ?key= query param; aiohttp/httpx status errors embed
        the full request URL (key included) in their message, so the key
        can reach an exception string even though we never format the URL
        ourselves."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider) -> str:
        """Get the static API key from the provider. Records it for
        scrubbing. No refresh path -- YouTube's key is static."""
        token = provider.current() or ""
        if token:
            self._seen_tokens.add(token)
        return token

    async def _request(self, endpoint: str, params: dict, token: str) -> Any:
        params = {**params, "key": token}
        return await self._fetch("GET", f"{_BASE}/{endpoint}", params=params)

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(
                content="'query' is required for youtube_search", is_error=True
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": max_results,
        }
        try:
            response = await self._request("search", params, token)
        except Exception as exc:
            logger.error("[youtube] youtube_search failed: %s", self._scrub(str(exc)))
            return ToolResult(
                content=self._scrub(f"YouTube search failed: {exc}"), is_error=True
            )
        if not isinstance(response, dict):
            return ToolResult(content="Unexpected YouTube response", is_error=True)
        return ToolResult(content=json.dumps({
            "results": _shape_search(response)[:max_results],
        }))

    async def _video(self, args: dict) -> ToolResult:
        video_id = args.get("video_id")
        if not video_id:
            return ToolResult(
                content="'video_id' is required for youtube_video", is_error=True
            )
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        params = {"part": "snippet,statistics", "id": video_id}
        try:
            response = await self._request("videos", params, token)
        except Exception as exc:
            logger.error("[youtube] youtube_video failed: %s", self._scrub(str(exc)))
            return ToolResult(
                content=self._scrub(f"YouTube video failed: {exc}"), is_error=True
            )
        if not isinstance(response, dict):
            return ToolResult(content="Unexpected YouTube response", is_error=True)
        items = response.get("items", []) or []
        if not items:
            return ToolResult(
                content=f"No video found for id '{video_id}'", is_error=True
            )
        return ToolResult(content=json.dumps(_shape_video(items[0])))

    async def _channel(self, args: dict) -> ToolResult:
        channel_id = args.get("channel_id")
        handle = args.get("handle")
        if bool(channel_id) == bool(handle):
            return ToolResult(
                content=(
                    "youtube_channel requires exactly one of 'channel_id' or "
                    "'handle'"
                ),
                is_error=True,
            )
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        params: dict = {"part": "snippet,statistics"}
        if channel_id:
            params["id"] = channel_id
            ref = f"id '{channel_id}'"
        else:
            params["forHandle"] = handle.lstrip("@")
            ref = f"handle '{handle}'"
        try:
            response = await self._request("channels", params, token)
        except Exception as exc:
            logger.error("[youtube] youtube_channel failed: %s", self._scrub(str(exc)))
            return ToolResult(
                content=self._scrub(f"YouTube channel failed: {exc}"), is_error=True
            )
        if not isinstance(response, dict):
            return ToolResult(content="Unexpected YouTube response", is_error=True)
        items = response.get("items", []) or []
        if not items:
            return ToolResult(
                content=f"No channel found for {ref}", is_error=True
            )
        return ToolResult(content=json.dumps(_shape_channel(items[0])))


def create(fetch_fn: FetchFn | None = None) -> YouTubePlugin:
    """Zero-arg-by-default factory the orchestrator discovers.

    `fetch_fn` is for tests; production leaves it None (uses
    _default_fetch's aiohttp→httpx fallback). The token provider is
    wired separately via the module-level `set_token_provider` setter
    from `cerebral/main.py` (Issue #148)."""
    return YouTubePlugin(fetch_fn=fetch_fn)
