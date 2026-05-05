"""
n8n MCP plugin tests — Issue #18.

TDD vertical slices for N8nPlugin:
  - list_workflows()
  - trigger_workflow(name, data)
  - get_workflow_result(id)

All HTTP calls are injected via fetch_fn so no live n8n instance is needed.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure plugins/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Shared fake n8n responses
# ---------------------------------------------------------------------------

_WORKFLOWS = [
    {"id": "w1", "name": "Send Slack Alert", "active": True},
    {"id": "w2", "name": "Daily Backup", "active": False},
]

_EXECUTION = {
    "id": "exec-42",
    "workflowId": "w1",
    "status": "success",
    "data": {"resultData": {"runData": {}}},
}


# ---------------------------------------------------------------------------
# Cycle 1 — list_workflows returns workflow list from API
# ---------------------------------------------------------------------------

class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_list_workflows_returns_names(self):
        """list_workflows returns a list of workflow names from the n8n API."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            assert method == "GET"
            assert "/api/v1/workflows" in url
            return {"data": _WORKFLOWS}

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool("list_workflows", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "workflows" in data
        names = [w["name"] for w in data["workflows"]]
        assert "Send Slack Alert" in names
        assert "Daily Backup" in names

    @pytest.mark.asyncio
    async def test_list_workflows_empty_returns_empty_list(self):
        """list_workflows with no workflows returns an empty list (not an error)."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": []}

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool("list_workflows", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["workflows"] == []


# ---------------------------------------------------------------------------
# Cycle 2 — list_workflows sends X-N8N-API-KEY auth header
# ---------------------------------------------------------------------------

class TestAuthHeader:
    @pytest.mark.asyncio
    async def test_list_workflows_sends_api_key_header(self):
        """list_workflows passes the API key in the X-N8N-API-KEY header."""
        from plugins.n8n import N8nPlugin

        seen_headers = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_headers.update(headers or {})
            return {"data": []}

        plugin = N8nPlugin(fetch_fn=fake_fetch, api_key="test-secret-key")
        await plugin.call_tool("list_workflows", {})
        assert seen_headers.get("X-N8N-API-KEY") == "test-secret-key"

    @pytest.mark.asyncio
    async def test_api_key_read_from_env(self, monkeypatch):
        """API key is read from N8N_API_KEY env var when not passed explicitly."""
        from plugins.n8n import create

        seen_headers = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_headers.update(headers or {})
            return {"data": []}

        monkeypatch.setenv("N8N_API_KEY", "env-driven-key")
        plugin = create(fetch_fn=fake_fetch)
        await plugin.call_tool("list_workflows", {})
        assert seen_headers.get("X-N8N-API-KEY") == "env-driven-key"

    @pytest.mark.asyncio
    async def test_api_key_has_sensible_default(self, monkeypatch):
        """When N8N_API_KEY is unset, a default is used (not empty, not crash)."""
        from plugins.n8n import create

        called = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            called["headers"] = headers or {}
            return {"data": []}

        monkeypatch.delenv("N8N_API_KEY", raising=False)
        plugin = create(fetch_fn=fake_fetch)
        await plugin.call_tool("list_workflows", {})
        # Must have the header — value is the default, not empty
        key = called["headers"].get("X-N8N-API-KEY", "")
        assert isinstance(key, str) and len(key) > 0


# ---------------------------------------------------------------------------
# Cycle 3 — trigger_workflow finds workflow by name and POSTs
# ---------------------------------------------------------------------------

class TestTriggerWorkflow:
    @pytest.mark.asyncio
    async def test_trigger_workflow_posts_to_webhook(self):
        """trigger_workflow(name, data) POSTs to the webhook endpoint for the workflow."""
        from plugins.n8n import N8nPlugin

        calls = []

        async def fake_fetch(method, url, *, headers=None, json=None):
            calls.append({"method": method, "url": url, "json": json})
            if method == "GET" and "/api/v1/workflows" in url:
                return {"data": _WORKFLOWS}
            if method == "POST":
                return {"executionId": "exec-99"}
            return {}

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool(
            "trigger_workflow",
            {"name": "Send Slack Alert", "data": {"channel": "#ops"}},
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert "execution_id" in data or "executionId" in data

        post_calls = [c for c in calls if c["method"] == "POST"]
        assert len(post_calls) == 1
        assert "Send Slack Alert" in post_calls[0]["url"] or "w1" in post_calls[0]["url"]

    @pytest.mark.asyncio
    async def test_trigger_workflow_passes_data_in_body(self):
        """trigger_workflow sends the data dict in the POST body."""
        from plugins.n8n import N8nPlugin

        posted_body = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            if method == "GET":
                return {"data": _WORKFLOWS}
            if method == "POST":
                posted_body.update(json or {})
                return {"executionId": "exec-100"}
            return {}

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        await plugin.call_tool(
            "trigger_workflow",
            {"name": "Send Slack Alert", "data": {"channel": "#ops", "msg": "hello"}},
        )
        assert posted_body.get("channel") == "#ops"
        assert posted_body.get("msg") == "hello"


# ---------------------------------------------------------------------------
# Cycle 4 — trigger_workflow with unknown name returns error
# ---------------------------------------------------------------------------

class TestTriggerWorkflowError:
    @pytest.mark.asyncio
    async def test_trigger_unknown_workflow_returns_error(self):
        """trigger_workflow returns an error ToolResult when the workflow name is not found."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            if method == "GET":
                return {"data": _WORKFLOWS}
            return {}

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool(
            "trigger_workflow",
            {"name": "Nonexistent Workflow", "data": {}},
        )
        assert result.is_error
        assert "not found" in result.content.lower() or "nonexistent" in result.content.lower()

    @pytest.mark.asyncio
    async def test_trigger_workflow_missing_name_arg_returns_error(self):
        """trigger_workflow returns an error when 'name' arg is missing."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _WORKFLOWS}

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool("trigger_workflow", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — get_workflow_result fetches execution by id
# ---------------------------------------------------------------------------

class TestGetWorkflowResult:
    @pytest.mark.asyncio
    async def test_get_workflow_result_returns_execution(self):
        """get_workflow_result(id) returns execution status and data."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            assert method == "GET"
            assert "exec-42" in url
            return _EXECUTION

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool("get_workflow_result", {"id": "exec-42"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data.get("status") == "success"
        assert data.get("id") == "exec-42"

    @pytest.mark.asyncio
    async def test_get_workflow_result_hits_executions_endpoint(self):
        """get_workflow_result calls the /api/v1/executions/{id} endpoint."""
        from plugins.n8n import N8nPlugin

        seen_url = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_url["url"] = url
            return _EXECUTION

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        await plugin.call_tool("get_workflow_result", {"id": "exec-42"})
        assert "/api/v1/executions/exec-42" in seen_url["url"]


# ---------------------------------------------------------------------------
# Cycle 6 — get_workflow_result not-found returns error
# ---------------------------------------------------------------------------

class TestGetWorkflowResultError:
    @pytest.mark.asyncio
    async def test_get_result_not_found_returns_error(self):
        """get_workflow_result returns an error when the execution id is not found."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise ValueError("404 Not Found")

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool("get_workflow_result", {"id": "bad-id"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_get_result_missing_id_arg_returns_error(self):
        """get_workflow_result returns an error when 'id' arg is missing."""
        from plugins.n8n import N8nPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            return _EXECUTION

        plugin = N8nPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool("get_workflow_result", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — list_tools exposes exactly 3 tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three_tools(self):
        """list_tools returns exactly list_workflows, trigger_workflow, get_workflow_result."""
        from plugins.n8n import N8nPlugin

        plugin = N8nPlugin()
        names = {t.name for t in plugin.list_tools()}
        assert names == {"list_workflows", "trigger_workflow", "get_workflow_result"}

    def test_list_tools_all_have_plugin_n8n(self):
        """Every tool returned by list_tools has plugin='n8n'."""
        from plugins.n8n import N8nPlugin

        plugin = N8nPlugin()
        for tool in plugin.list_tools():
            assert tool.plugin == "n8n"

    def test_unknown_tool_returns_error(self):
        """call_tool with an unknown tool name returns a ToolResult with is_error=True."""
        import asyncio
        from plugins.n8n import N8nPlugin

        plugin = N8nPlugin()
        result = asyncio.get_event_loop().run_until_complete(
            plugin.call_tool("does_not_exist", {})
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — create() factory
# ---------------------------------------------------------------------------

class TestCreateFactory:
    def test_create_returns_n8n_plugin(self):
        """create() returns an N8nPlugin instance."""
        from plugins.n8n import create, N8nPlugin

        plugin = create()
        assert isinstance(plugin, N8nPlugin)

    def test_create_plugin_name_is_n8n(self):
        """Plugin name is 'n8n' for MCP orchestrator auto-registration."""
        from plugins.n8n import create

        assert create().name == "n8n"

    def test_create_accepts_fetch_fn(self):
        """create() accepts an optional fetch_fn for testing."""
        from plugins.n8n import create

        sentinel = object()
        plugin = create(fetch_fn=sentinel)
        assert plugin._fetch is sentinel
