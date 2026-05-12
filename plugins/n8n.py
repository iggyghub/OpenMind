"""
n8n plugin — MCP server for Felix.

Tools: list_workflows, trigger_workflow, get_workflow_result.
Bridges Felix to a self-hosted n8n instance via its REST API.

All HTTP calls are injectable via fetch_fn so tests run without a live n8n.
Auth token is read from N8N_API_KEY env var (default: "changeme").
"""
import json
import logging
import os
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "n8n"

# ADR-0005 / Issue #44 — list_workflows / trigger_workflow / get_workflow_result
# all hit the local n8n REST API. n8n is the bridge to remote services but
# the plugin itself only opens a local socket.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"network_egress_local"})

_DEFAULT_API_KEY = "changeme"
_DEFAULT_BASE_URL = "http://localhost:5678"

FetchFn = Callable[..., Awaitable[dict]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None, json: dict | None = None) -> dict:
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, json=json) as resp:
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, json=json)
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class N8nPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._fetch = fetch_fn or _default_fetch
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key or os.environ.get("N8N_API_KEY", _DEFAULT_API_KEY)

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="list_workflows",
                description="List all workflows configured in the local n8n instance.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="trigger_workflow",
                description=(
                    "Trigger an n8n workflow by name. "
                    "Posts data to the workflow's webhook endpoint and returns an execution id."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact workflow name as shown in n8n"},
                        "data": {"type": "object", "description": "Payload sent to the workflow"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="get_workflow_result",
                description="Fetch the result of a previously triggered workflow execution by its id.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Execution id returned by trigger_workflow"},
                    },
                    "required": ["id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "list_workflows":
            return await self._list_workflows()
        if tool_name == "trigger_workflow":
            return await self._trigger_workflow(args)
        if tool_name == "get_workflow_result":
            return await self._get_workflow_result(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        return {"X-N8N-API-KEY": self._api_key}

    async def _list_workflows(self) -> ToolResult:
        try:
            url = f"{self._base_url}/api/v1/workflows"
            resp = await self._fetch("GET", url, headers=self._auth_headers())
            workflows = resp.get("data", [])
            return ToolResult(content=json.dumps({"workflows": workflows}))
        except Exception as exc:
            return ToolResult(content=f"Failed to list workflows: {exc}", is_error=True)

    async def _trigger_workflow(self, args: dict) -> ToolResult:
        name = args.get("name")
        if not name:
            return ToolResult(content="'name' argument is required", is_error=True)
        data = args.get("data") or {}

        # Resolve workflow id by name
        try:
            url = f"{self._base_url}/api/v1/workflows"
            resp = await self._fetch("GET", url, headers=self._auth_headers())
            workflows = resp.get("data", [])
        except Exception as exc:
            return ToolResult(content=f"Failed to list workflows: {exc}", is_error=True)

        match = next((w for w in workflows if w.get("name") == name), None)
        if match is None:
            return ToolResult(
                content=f"Workflow not found: '{name}'",
                is_error=True,
            )

        # Trigger via webhook URL (community-edition compatible)
        webhook_url = f"{self._base_url}/webhook/{name}"
        try:
            result = await self._fetch("POST", webhook_url, headers=self._auth_headers(), json=data)
        except Exception as exc:
            return ToolResult(content=f"Failed to trigger workflow '{name}': {exc}", is_error=True)

        execution_id = result.get("executionId") or result.get("execution_id", "")
        return ToolResult(content=json.dumps({"execution_id": execution_id, "workflow": name}))

    async def _get_workflow_result(self, args: dict) -> ToolResult:
        exec_id = args.get("id")
        if not exec_id:
            return ToolResult(content="'id' argument is required", is_error=True)
        try:
            url = f"{self._base_url}/api/v1/executions/{exec_id}"
            resp = await self._fetch("GET", url, headers=self._auth_headers())
            return ToolResult(content=json.dumps(resp))
        except Exception as exc:
            return ToolResult(content=f"Failed to get execution '{exec_id}': {exc}", is_error=True)


def create(fetch_fn: FetchFn | None = None, base_url: str | None = None, api_key: str | None = None) -> N8nPlugin:
    return N8nPlugin(fetch_fn=fetch_fn, base_url=base_url, api_key=api_key)
