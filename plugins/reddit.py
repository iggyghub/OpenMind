"""
Reddit MCP plugin — Issue #97.

Tools: reddit_subreddit, reddit_search.

Stateless, read-only over Reddit's public JSON API — no authentication, no
token storage, no SQLite:
  - Subreddit listing: https://www.reddit.com/r/<sub>/<sort>.json
  - Search:            https://www.reddit.com/search.json
                       https://www.reddit.com/r/<sub>/search.json

Clones the plugins/wikipedia.py posture (public API, injectable async
fetch_fn, JSON response shaping). The one structural deviation: the default
transport injects an explicit User-Agent header — Reddit returns HTTP 429 for
generic/default user agents.

The default fetch_fn tries aiohttp then httpx — same lazy-import pattern as
plugins/wikipedia.py / plugins/n8n.py / plugins/http_client.py. Tests inject
a stub.
"""
import json
import logging
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "reddit"

# ADR-0005 — reddit_subreddit / reddit_search fetch post listings from
# www.reddit.com over the public internet. Identical surface to wikipedia.py
# / news.py: a cloud read, no secrets, no write.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
})

_BASE = "https://www.reddit.com"
_USER_AGENT = "OpenMind-Reddit/1.0"
_DEFAULT_LIMIT = 10
_SELFTEXT_CAP = 500

_SUBREDDIT_SORTS = ("hot", "new", "top")
_SEARCH_SORTS = ("relevance", "hot", "top", "new")
_TOP_TIMES = ("hour", "day", "week", "month", "year", "all")

FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp → httpx fallback. Returns parsed JSON.

    Injects a User-Agent header (merged over any caller-supplied headers) —
    Reddit 429s generic/default agents.
    """
    merged_headers = {"User-Agent": _USER_AGENT}
    if headers:
        merged_headers.update(headers)
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=merged_headers,
                                       params=params, json=json) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=merged_headers,
                                        params=params, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


def _shape(payload: Any) -> list[dict]:
    """Extract shaped posts from a Reddit listing response.

    Reddit listings are {"data": {"children": [{"data": {...}}, ...]}}.
    """
    if not isinstance(payload, dict):
        return []
    children = payload.get("data", {}).get("children", []) or []
    posts: list[dict] = []
    for child in children:
        data = child.get("data", {}) if isinstance(child, dict) else {}
        selftext = data.get("selftext", "") or ""
        if len(selftext) > _SELFTEXT_CAP:
            selftext = selftext[:_SELFTEXT_CAP]
        permalink = data.get("permalink", "") or ""
        posts.append({
            "title": data.get("title", ""),
            "author": data.get("author", ""),
            "score": data.get("score", 0),
            "num_comments": data.get("num_comments", 0),
            "permalink": f"{_BASE.replace('www.', '')}{permalink}" if permalink else "",
            "url": data.get("url", ""),
            "subreddit": data.get("subreddit", ""),
            "created_utc": data.get("created_utc", 0),
            "selftext": selftext,
        })
    return posts


class RedditPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="reddit_subreddit",
                description=(
                    "List posts from a subreddit. Sort by hot, new, or top "
                    "(top accepts a time window)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "subreddit": {
                            "type": "string",
                            "description": "Subreddit name (without the r/ prefix).",
                        },
                        "sort": {
                            "type": "string",
                            "description": "hot | new | top (default hot).",
                        },
                        "time": {
                            "type": "string",
                            "description": (
                                "For sort=top: hour | day | week | month | "
                                "year | all (default day)."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum posts to return (default {_DEFAULT_LIMIT}).",
                        },
                    },
                    "required": ["subreddit"],
                },
            ),
            Tool(
                name="reddit_search",
                description=(
                    "Search Reddit posts by query, optionally restricted to a "
                    "single subreddit."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search term."},
                        "subreddit": {
                            "type": "string",
                            "description": "Restrict the search to this subreddit.",
                        },
                        "sort": {
                            "type": "string",
                            "description": "relevance | hot | top | new (default relevance).",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum posts to return (default {_DEFAULT_LIMIT}).",
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "reddit_subreddit":
            return await self._subreddit(args)
        if tool_name == "reddit_search":
            return await self._search(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _subreddit(self, args: dict) -> ToolResult:
        subreddit = args.get("subreddit")
        if not subreddit:
            return ToolResult(
                content="'subreddit' is required for reddit_subreddit",
                is_error=True,
            )
        sort = (args.get("sort") or "hot").lower()
        if sort not in _SUBREDDIT_SORTS:
            return ToolResult(
                content=f"Invalid sort: '{sort}' (expected one of {', '.join(_SUBREDDIT_SORTS)})",
                is_error=True,
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params: dict = {"limit": max_results}
        if sort == "top":
            time_window = (args.get("time") or "day").lower()
            if time_window not in _TOP_TIMES:
                return ToolResult(
                    content=f"Invalid time: '{time_window}' (expected one of {', '.join(_TOP_TIMES)})",
                    is_error=True,
                )
            params["t"] = time_window

        url = f"{_BASE}/r/{subreddit}/{sort}.json"
        try:
            response = await self._fetch("GET", url, params=params)
        except Exception as exc:
            logger.error("[reddit] reddit_subreddit failed: %s", exc)
            return ToolResult(content=f"Reddit request failed: {exc}", is_error=True)

        if not isinstance(response, dict):
            return ToolResult(content="Unexpected Reddit response", is_error=True)
        return ToolResult(content=json.dumps({
            "posts": _shape(response)[:max_results],
        }))

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(
                content="'query' is required for reddit_search", is_error=True
            )
        sort = (args.get("sort") or "relevance").lower()
        if sort not in _SEARCH_SORTS:
            return ToolResult(
                content=f"Invalid sort: '{sort}' (expected one of {', '.join(_SEARCH_SORTS)})",
                is_error=True,
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        subreddit = args.get("subreddit")
        params: dict = {"q": query, "limit": max_results, "sort": sort}
        if subreddit:
            params["restrict_sr"] = 1
            url = f"{_BASE}/r/{subreddit}/search.json"
        else:
            url = f"{_BASE}/search.json"

        try:
            response = await self._fetch("GET", url, params=params)
        except Exception as exc:
            logger.error("[reddit] reddit_search failed: %s", exc)
            return ToolResult(content=f"Reddit request failed: {exc}", is_error=True)

        if not isinstance(response, dict):
            return ToolResult(content="Unexpected Reddit response", is_error=True)
        return ToolResult(content=json.dumps({
            "posts": _shape(response)[:max_results],
        }))


def create(fetch_fn: FetchFn | None = None) -> RedditPlugin:
    return RedditPlugin(fetch_fn=fetch_fn)
