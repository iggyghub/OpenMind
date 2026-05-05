"""
HTTP client MCP plugin — Issue #24.

Generic HTTP client for hitting arbitrary endpoints (API testing, webhooks,
status checks).

Tools: http_get, http_post, http_put, http_delete.

Each takes (url, headers?, body?) and returns {status, body}. JSON
responses are parsed; non-JSON responses come back as raw text.

The default fetch_fn tries aiohttp then httpx — same fallback pattern as
plugins/n8n.py — and returns a normalised dict
`{"status": int, "body": ..., "headers": dict}`. Tests pass a stub.
"""
import json
import logging
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "http_client"

FetchFn = Callable[..., Awaitable[dict]]

_TOOL_TO_METHOD = {
    "http_get": "GET",
    "http_post": "POST",
    "http_put": "PUT",
    "http_delete": "DELETE",
}


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         json: dict | None = None) -> dict:
    """Default transport: aiohttp → httpx fallback. Returns a normalised dict."""
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, json=json) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    body: Any = await resp.json()
                else:
                    body = await resp.text()
                return {
                    "status": resp.status,
                    "body": body,
                    "headers": dict(resp.headers),
                }
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, json=json)
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                body = resp.json()
            else:
                body = resp.text
            return {
                "status": resp.status_code,
                "body": body,
                "headers": dict(resp.headers),
            }
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class HttpClientPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    def list_tools(self) -> list[Tool]:
        url_prop = {"type": "string", "description": "Full URL including scheme."}
        headers_prop = {
            "type": "object",
            "description": "Optional headers to send with the request.",
        }
        body_prop = {
            "type": "object",
            "description": "JSON body to send (for POST/PUT).",
        }
        return [
            Tool(
                name="http_get",
                description="Send a GET request and return {status, body}.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"url": url_prop, "headers": headers_prop},
                    "required": ["url"],
                },
            ),
            Tool(
                name="http_post",
                description="Send a POST request with an optional JSON body.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": url_prop,
                        "headers": headers_prop,
                        "body": body_prop,
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="http_put",
                description="Send a PUT request with an optional JSON body.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": url_prop,
                        "headers": headers_prop,
                        "body": body_prop,
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="http_delete",
                description="Send a DELETE request.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"url": url_prop, "headers": headers_prop},
                    "required": ["url"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        method = _TOOL_TO_METHOD.get(tool_name)
        if method is None:
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

        url = args.get("url")
        if not url:
            return ToolResult(content=f"'url' is required for {tool_name}", is_error=True)

        headers = args.get("headers")
        body = args.get("body")

        try:
            response = await self._fetch(method, url, headers=headers, json=body)
        except Exception as exc:
            return ToolResult(content=f"HTTP {method} failed: {exc}", is_error=True)

        status = response.get("status", 0)
        payload = json.dumps({
            "status": status,
            "body": response.get("body"),
        })
        return ToolResult(content=payload, is_error=not (200 <= status < 400))


def create(fetch_fn: FetchFn | None = None) -> HttpClientPlugin:
    return HttpClientPlugin(fetch_fn=fetch_fn)
