"""
Wikipedia MCP plugin — Issue #25.

Tools: wiki_search, wiki_summary.

Uses the public Wikipedia APIs — no API key required:
  - Search: https://en.wikipedia.org/w/api.php (opensearch action)
  - Summary: https://en.wikipedia.org/api/rest_v1/page/summary/{title}

The default fetch_fn tries aiohttp then httpx — same pattern as plugins/n8n.py
and plugins/http_client.py. Tests inject a stub.
"""
import json
import logging
import urllib.parse
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "wikipedia"
_ACTION_API = "https://en.wikipedia.org/w/api.php"
_REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_DEFAULT_LIMIT = 5

FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp → httpx fallback. Returns parsed JSON."""
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, params=params, json=json) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class WikipediaPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="wiki_search",
                description=(
                    "Search Wikipedia for a query and return matching article titles, "
                    "short descriptions and URLs."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search term"},
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum results to return (default {_DEFAULT_LIMIT})",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="wiki_summary",
                description=(
                    "Fetch the lead-section summary of a Wikipedia article by exact title."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Exact article title"},
                    },
                    "required": ["title"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "wiki_search":
            return await self._search(args)
        if tool_name == "wiki_summary":
            return await self._summary(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(content="'query' is required for wiki_search", is_error=True)
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params = {
            "action": "opensearch",
            "format": "json",
            "search": query,
            "limit": max_results,
        }
        try:
            response = await self._fetch("GET", _ACTION_API, params=params)
        except Exception as exc:
            logger.error("[wikipedia] wiki_search failed: %s", exc)
            return ToolResult(content=f"Wikipedia search failed: {exc}", is_error=True)

        results: list[dict] = []
        if isinstance(response, list) and len(response) >= 4:
            titles = response[1] or []
            descriptions = response[2] or []
            urls = response[3] or []
            for i, title in enumerate(titles):
                results.append({
                    "title": title,
                    "description": descriptions[i] if i < len(descriptions) else "",
                    "url": urls[i] if i < len(urls) else "",
                })
        return ToolResult(content=json.dumps({"results": results}))

    async def _summary(self, args: dict) -> ToolResult:
        title = args.get("title")
        if not title:
            return ToolResult(content="'title' is required for wiki_summary", is_error=True)
        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
        url = f"{_REST_SUMMARY}{encoded}"
        try:
            response = await self._fetch("GET", url)
        except Exception as exc:
            logger.error("[wikipedia] wiki_summary failed: %s", exc)
            return ToolResult(content=f"Wikipedia summary failed: {exc}", is_error=True)

        if not isinstance(response, dict):
            return ToolResult(content="Unexpected Wikipedia response", is_error=True)
        page_url = (
            response.get("content_urls", {})
            .get("desktop", {})
            .get("page", "")
        )
        return ToolResult(content=json.dumps({
            "title": response.get("title", title),
            "description": response.get("description", ""),
            "extract": response.get("extract", ""),
            "url": page_url,
        }))


def create(fetch_fn: FetchFn | None = None) -> WikipediaPlugin:
    return WikipediaPlugin(fetch_fn=fetch_fn)
