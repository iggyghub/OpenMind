"""
TODO: one-line description of what this plugin does.

Tools: TODO_TOOL_NAME (read-only), TODO_WRITE_TOOL (write, irreversible).

Static API-token plugin -- mirrors plugins/todoist.py spine.
Token arrives via set_token_provider() wired from cerebral/main.py.
Tests inject a stub provider + stub fetch_fn (no real network or keyring).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "TODO"  # must match filename stem, e.g. "weather" for plugins/weather.py

# ADR-0005 / Issue #TODO.
#   Declare every capability class your tools touch.
#   - secrets_read: DELIBERATE over-declaration (todoist/youtube posture B) --
#     the AST audit maps secrets_read only to keyring.* calls; provider.current()
#     is not auto-mapped. Declare it anyway: this plugin reads credential material.
#     Over-declaration is audit-safe (_inspect fails only on UNDER-declaration).
#   - external_data_read: hand-declared (AST never auto-requires external_data_*).
#     Add external_data_write if any tool mutates an external account.
#   - network_egress_cloud: auto-required by the AST scanner for aiohttp/httpx calls.
#   Remove classes that do not apply; add classes that do (see SKILL.md for full vocab).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "network_egress_cloud",
    # "external_data_write",  # uncomment if any tool writes to an external account
})

_BASE = "https://api.TODO.com"  # replace with real API base URL

_FACTORY_NOT_WIRED_MSG = "TODO plugin is not available -- token provider not wired"
_NO_TOKEN_MSG = "TODO API token is not configured"

FetchFn = Callable[..., Awaitable[Any]]


class TokenProvider(Protocol):
    """Per-active-profile API-token handle wired from cerebral/main.py.

    Carries ONLY current() because this is a static (user-rotated) API token.
    There is no OAuth refresh, so the Protocol does not pretend one exists.
    Mirrors plugins/todoist.py / plugins/notion.py / plugins/toggl.py.
    """

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire cerebral/main.py token factory -- called once at orchestrator startup."""
    global _token_provider_factory
    _token_provider_factory = fn


class TODOPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        token_provider: Optional[TokenProvider] = None,
        fetch_fn: Optional[FetchFn] = None,
    ) -> None:
        self._provider = token_provider
        self._fetch = fetch_fn or self._default_fetch

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="TODO_read_tool",
                description="TODO: describe what this tool returns.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "TODO"},
                    },
                    "required": ["query"],
                },
            ),
            # Irreversible write tool example -- uncomment and adapt as needed:
            # Tool(
            #     name="TODO_delete_item",
            #     description="TODO: describe the irreversible action.",
            #     plugin=PLUGIN_NAME,
            #     irreversible=True,  # triggers alwaysOnTop modal (ADR-0005)
            #     schema={
            #         "type": "object",
            #         "properties": {
            #             "id": {"type": "string", "description": "Item id to delete"},
            #         },
            #         "required": ["id"],
            #     },
            # ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        provider = self._resolve_provider()
        if provider is None:
            return ToolResult(content=_FACTORY_NOT_WIRED_MSG, is_error=True)
        token = provider.current()
        if not token:
            return ToolResult(content=_NO_TOKEN_MSG, is_error=True)

        if tool_name == "TODO_read_tool":
            return await self._read_tool(args, token)
        return ToolResult(content=f"Unknown tool: {tool_name!r}", is_error=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> Optional[TokenProvider]:
        if self._provider is not None:
            return self._provider
        if _token_provider_factory is not None:
            return _token_provider_factory()
        return None

    async def _read_tool(self, args: dict, token: str) -> ToolResult:
        query = args.get("query", "")
        # TODO: replace with real API call
        url = f"{_BASE}/TODO_endpoint"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            data = await self._fetch(url, headers=headers, params={"q": query})
            return ToolResult(content=json.dumps(data))
        except Exception as exc:
            logger.exception("[TODO] read_tool failed")
            return ToolResult(content=str(exc), is_error=True)

    async def _default_fetch(self, url: str, **kwargs: Any) -> Any:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, **kwargs) as resp:
                resp.raise_for_status()
                return await resp.json()


def create() -> TODOPlugin:
    """Zero-arg factory called by the orchestrator at plugin registration."""
    return TODOPlugin()
