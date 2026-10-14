"""
Reddit MCP plugin — Issue #97.

Tools: reddit_subreddit, reddit_search. Quick, read-only post LISTINGS for
chat ("what's top in r/x this week?"). For reading whole discussions into
knowledge, see plugins/reddit_ingest.py (the Library's Reddit tab).

Transport: Reddit's public Atom feeds, no key, no storage:
  - Subreddit: https://www.reddit.com/r/<sub>/<sort>/.rss[?t=<time>]
  - Search:    https://www.reddit.com/search.rss  |  /r/<sub>/search.rss
2026-10-11: the original public ``.json`` endpoints now return 403 "Blocked"
for unauthenticated clients, so this rides the same feeds (and parser) as
cerebral.video.web_source. Feeds carry no score / comment counts; those
fields were dropped. Unauthenticated Reddit allows ~1 request/minute.

fetch_fn(url) -> feed XML text is injectable; tests pass a stub.
"""
import asyncio
import json
import logging
import urllib.parse
from typing import Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import web_source as _ws

logger = logging.getLogger(__name__)

PLUGIN_NAME = "reddit"

# ADR-0005 — a cloud read of public posts, no secrets, no write.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
})

_BASE = "https://www.reddit.com"
_DEFAULT_LIMIT = 10
_SELFTEXT_CAP = 500

_SUBREDDIT_SORTS = ("hot", "new", "top")
_SEARCH_SORTS = ("relevance", "hot", "top", "new")
_TOP_TIMES = ("hour", "day", "week", "month", "year", "all")

FetchFn = Callable[[str], Awaitable[str]]


async def _default_fetch(url: str) -> str:
    return await asyncio.to_thread(_ws._fetch_fn, url)


def _shape(xml: str) -> list[dict]:
    """Posts (t3_ entries) from a Reddit Atom feed."""
    posts = []
    for e in _ws._entries(xml):
        if not e["id"].startswith("t3_"):
            continue
        parsed = _ws.parse_reddit_url(e["link"]) or {}
        posts.append({
            "title": e["title"],
            "author": e["author"].removeprefix("/u/"),
            "permalink": e["link"],
            "subreddit": parsed.get("sub", ""),
            "selftext": e["text"][:_SELFTEXT_CAP],
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
                    "List posts from a subreddit (titles, authors, links, opening "
                    "text). Sort by hot, new, or top (top accepts a time window). "
                    "To read whole threads with their comments into knowledge, use "
                    "reddit_ingest instead."
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

    async def _get(self, url: str, params: dict, max_results: int) -> ToolResult:
        full = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            posts = _shape(await self._fetch(full))
        except Exception as exc:  # noqa: BLE001
            logger.error("[reddit] request failed: %s", exc)
            return ToolResult(content=f"Reddit request failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({"posts": posts[:max_results]}))

    async def _subreddit(self, args: dict) -> ToolResult:
        subreddit = (args.get("subreddit") or "").strip().removeprefix("r/")
        if not subreddit:
            return ToolResult(content="'subreddit' is required for reddit_subreddit", is_error=True)
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
        return await self._get(f"{_BASE}/r/{subreddit}/{sort}/.rss", params, max_results)

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(content="'query' is required for reddit_search", is_error=True)
        sort = (args.get("sort") or "relevance").lower()
        if sort not in _SEARCH_SORTS:
            return ToolResult(
                content=f"Invalid sort: '{sort}' (expected one of {', '.join(_SEARCH_SORTS)})",
                is_error=True,
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        subreddit = (args.get("subreddit") or "").strip().removeprefix("r/")
        params: dict = {"q": query, "limit": max_results, "sort": sort}
        if subreddit:
            params["restrict_sr"] = 1
            url = f"{_BASE}/r/{subreddit}/search.rss"
        else:
            url = f"{_BASE}/search.rss"
        return await self._get(url, params, max_results)


def create(fetch_fn: FetchFn | None = None) -> RedditPlugin:
    return RedditPlugin(fetch_fn=fetch_fn)
