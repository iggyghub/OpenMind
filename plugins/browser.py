"""
Browser plugin — MCP server for Felix.

Tools: web_search, navigate, read_pdf.
Uses OpenClaw's bundled Playwright + Mozilla Readability + PDF.js via HTTP.

All HTTP side-effects are injectable via fetch_fn for testing.
"""
import json
import logging
from typing import Callable, Awaitable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "browser"
OPENCLAW_BASE = "http://localhost:3000"

# ADR-0005 / Issue #44 — web_search / navigate / read_pdf all POST to the
# local OpenClaw bridge (network_egress_local) which fetches and returns
# external web/PDF content (external_data_read).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_local",
})


async def _default_fetch(url: str, body: dict) -> dict:
    """POST body to url and return parsed JSON response."""
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body) as resp:
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=body)
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class BrowserPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: Callable[[str, dict], Awaitable[dict]] | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="web_search",
                description=(
                    "Search the web for a query and return top results "
                    "(title, URL, snippet). Uses OpenClaw Playwright headlessly."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default 5)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="navigate",
                description=(
                    "Navigate to a URL and extract the main readable content "
                    "using Mozilla Readability. No visible browser window opened."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to navigate to and extract content from",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="read_pdf",
                description=(
                    "Fetch a PDF from a URL and extract its text content "
                    "using OpenClaw's bundled PDF.js."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL of the PDF to read",
                        },
                    },
                    "required": ["url"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "web_search":
            return await self._web_search(args)
        if tool_name == "navigate":
            return await self._navigate(args)
        if tool_name == "read_pdf":
            return await self._read_pdf(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    async def _web_search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(content="'query' is required", is_error=True)
        max_results = args.get("max_results", 5)
        try:
            data = await self._fetch(
                f"{OPENCLAW_BASE}/browser/search",
                {"query": query, "max_results": max_results},
            )
        except Exception as exc:
            logger.error("[browser] web_search failed: %s", exc)
            return ToolResult(content=f"Search failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps(data))

    async def _navigate(self, args: dict) -> ToolResult:
        url = args.get("url")
        if not url:
            return ToolResult(content="'url' is required", is_error=True)
        try:
            data = await self._fetch(
                f"{OPENCLAW_BASE}/browser/navigate",
                {"url": url},
            )
        except Exception as exc:
            logger.error("[browser] navigate failed: %s", exc)
            return ToolResult(content=f"Navigate failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({"url": url, "content": data.get("content", "")}))

    async def _read_pdf(self, args: dict) -> ToolResult:
        url = args.get("url")
        if not url:
            return ToolResult(content="'url' is required", is_error=True)
        try:
            data = await self._fetch(
                f"{OPENCLAW_BASE}/browser/pdf",
                {"url": url},
            )
        except Exception as exc:
            logger.error("[browser] read_pdf failed: %s", exc)
            return ToolResult(content=f"PDF read failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({"url": url, "text": data.get("text", "")}))


def create() -> BrowserPlugin:
    return BrowserPlugin()
