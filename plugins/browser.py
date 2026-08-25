"""
Browser plugin — MCP server for Felix.

Tools: web_search, navigate, read_pdf.

Runs through OpenClaw's `infer web` CLI surface (`openclaw infer web
search`/`fetch`), not an HTTP POST. The plugin used to POST to
`http://localhost:3000/browser/*` -- that endpoint has never existed in any
installed OpenClaw version (same "phantom-:3000" pattern issue #378 found
and fixed for the LLM backend; this plugin was the other, un-fixed instance
of it -- OpenClaw 2026.5.28 exposes web search/fetch only via its CLI
(`openclaw infer web ...`) or gateway RPC, never a bare REST endpoint).
Confirmed 2026-08-25: `openclaw infer web search --json` returns real
DuckDuckGo results with no provider key needed; `openclaw infer web fetch`
requires a configured fetch provider (only `firecrawl` today, needing
`FIRECRAWL_API_KEY`) -- `navigate`/`read_pdf` route through the same real
CLI command so they'll start working the moment such a key is set, but
until then correctly report OpenClaw's own "no provider" error instead of
a fake connection failure.

All CLI side-effects are injectable via run_cli_fn for testing.
"""
import asyncio
import json
import logging
from typing import Callable, Awaitable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "browser"

# ADR-0005 / Issue #44 — web_search / navigate / read_pdf all shell out to
# the local `openclaw` CLI (network_egress_local), which fetches and
# returns external web/PDF content (external_data_read).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_local",
})


async def _default_run_cli(args: list[str]) -> dict:
    """Run `openclaw <args>`, parse its --json stdout.

    `openclaw` is an npm-installed .cmd shim on Windows -- CreateProcess
    can't resolve a bare .cmd via create_subprocess_exec (confirmed: raises
    FileNotFoundError even though `openclaw ...` works fine from a real
    shell), so it's invoked through `cmd /c`. Each argv element still
    travels as its own process argument (no shell string is built or
    interpolated), so a query containing quotes/spaces needs no escaping
    here and isn't a shell-injection surface.
    """
    proc = await asyncio.create_subprocess_exec(
        "cmd", "/c", "openclaw", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError((err or out).decode("utf-8", errors="replace").strip() or f"openclaw exited {proc.returncode}")
    try:
        return json.loads(out.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"openclaw returned non-JSON output: {out[:200]!r}") from exc


class BrowserPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_cli_fn: Callable[[list[str]], Awaitable[dict]] | None = None) -> None:
        self._run_cli = run_cli_fn or _default_run_cli

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="web_search",
                description=(
                    "Search the web for a query and return top results "
                    "(title, URL, snippet). Uses OpenClaw's web search CLI."
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
                    "Navigate to a URL and extract its main readable content. "
                    "No visible browser window opened."
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
                    "Fetch a PDF from a URL and extract its text content."
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
            data = await self._run_cli([
                "infer", "web", "search",
                "--query", str(query), "--limit", str(max_results), "--json",
            ])
            outputs = data.get("outputs") or []
            result = outputs[0]["result"] if outputs else {}
            hits = result.get("results", [])
        except Exception as exc:
            logger.error("[browser] web_search failed: %s", exc)
            return ToolResult(content=f"Search failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({
            "results": [
                {"title": h.get("title", ""), "url": h.get("url", ""), "snippet": h.get("snippet", "")}
                for h in hits
            ],
        }))

    async def _navigate(self, args: dict) -> ToolResult:
        url = args.get("url")
        if not url:
            return ToolResult(content="'url' is required", is_error=True)
        try:
            data = await self._run_cli(["infer", "web", "fetch", "--url", str(url), "--json"])
            outputs = data.get("outputs") or []
            result = outputs[0]["result"] if outputs else {}
            content = result.get("content") or result.get("text") or ""
        except Exception as exc:
            logger.error("[browser] navigate failed: %s", exc)
            return ToolResult(content=f"Navigate failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({"url": url, "content": content}))

    async def _read_pdf(self, args: dict) -> ToolResult:
        url = args.get("url")
        if not url:
            return ToolResult(content="'url' is required", is_error=True)
        try:
            data = await self._run_cli([
                "infer", "web", "fetch", "--url", str(url), "--format", "text", "--json",
            ])
            outputs = data.get("outputs") or []
            result = outputs[0]["result"] if outputs else {}
            text = result.get("text") or result.get("content") or ""
        except Exception as exc:
            logger.error("[browser] read_pdf failed: %s", exc)
            return ToolResult(content=f"PDF read failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({"url": url, "text": text}))


def create() -> BrowserPlugin:
    return BrowserPlugin()
