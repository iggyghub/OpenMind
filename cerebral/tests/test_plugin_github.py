"""
GitHub MCP plugin tests — Issue #24.

Delegates HTTP to an injected N8nPlugin, mirroring GoogleWorkspacePlugin (#20).

Tools: github_list_issues, github_create_issue, github_list_prs, github_get_notifications.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_fetch(workflow_name: str, *, captured: dict | None = None,
                response: dict | None = None):
    """Mimic n8n: GET /api/v1/workflows returns the named workflow,
    POST /webhook/<name> returns either an executionId or canned data."""
    async def fake_fetch(method, url, *, headers=None, json=None):
        if method == "GET" and "/api/v1/workflows" in url:
            return {"data": [{"id": "w-gh", "name": workflow_name, "active": True}]}
        if method == "POST":
            if captured is not None:
                captured["payload"] = json or {}
                captured["url"] = url
            return response or {"executionId": "exec-1"}
        return {}
    return fake_fetch


def _error_fetch():
    async def fake_fetch(method, url, *, headers=None, json=None):
        raise ConnectionError("n8n unreachable")
    return fake_fetch


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_four(self):
        from plugins.github import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "github_list_issues",
            "github_create_issue",
            "github_list_prs",
            "github_get_notifications",
        }

    def test_create_plugin_named_github(self):
        from plugins.github import create

        assert create().name == "github"

    def test_all_tools_have_correct_plugin_name(self):
        from plugins.github import create

        for tool in create().list_tools():
            assert tool.plugin == "github"


# ---------------------------------------------------------------------------
# Cycle 2 — github_list_issues triggers the right workflow
# ---------------------------------------------------------------------------

class TestListIssues:
    @pytest.mark.asyncio
    async def test_list_issues_requires_repo(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub List Issues"))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("github_list_issues", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_list_issues_calls_correct_workflow(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        captured: dict = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub List Issues", captured=captured))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool(
            "github_list_issues", {"repo": "iggyghub/OpenMind"}
        )
        assert not result.is_error
        assert captured["payload"]["repo"] == "iggyghub/OpenMind"
        assert "Felix GitHub List Issues" in captured["url"]


# ---------------------------------------------------------------------------
# Cycle 3 — github_create_issue requires repo + title
# ---------------------------------------------------------------------------

class TestCreateIssue:
    @pytest.mark.asyncio
    async def test_create_issue_missing_repo_returns_error(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub Create Issue"))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool(
            "github_create_issue", {"title": "x"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_create_issue_missing_title_returns_error(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub Create Issue"))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool(
            "github_create_issue", {"repo": "x/y"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_create_issue_passes_payload(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        captured: dict = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub Create Issue", captured=captured))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool(
            "github_create_issue",
            {
                "repo": "iggyghub/OpenMind",
                "title": "Bug: foo",
                "body": "Reproduction steps...",
            },
        )
        assert not result.is_error
        payload = captured["payload"]
        assert payload["repo"] == "iggyghub/OpenMind"
        assert payload["title"] == "Bug: foo"
        assert payload["body"] == "Reproduction steps..."
        assert "Felix GitHub Create Issue" in captured["url"]


# ---------------------------------------------------------------------------
# Cycle 4 — github_list_prs
# ---------------------------------------------------------------------------

class TestListPrs:
    @pytest.mark.asyncio
    async def test_list_prs_requires_repo(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub List PRs"))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("github_list_prs", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_list_prs_calls_correct_workflow(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        captured: dict = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub List PRs", captured=captured))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool(
            "github_list_prs", {"repo": "iggyghub/OpenMind"}
        )
        assert not result.is_error
        assert "Felix GitHub List PRs" in captured["url"]


# ---------------------------------------------------------------------------
# Cycle 5 — github_get_notifications (no required args)
# ---------------------------------------------------------------------------

class TestNotifications:
    @pytest.mark.asyncio
    async def test_notifications_calls_correct_workflow(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        captured: dict = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub Notifications", captured=captured))
        plugin = GithubPlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("github_get_notifications", {})
        assert not result.is_error
        assert "Felix GitHub Notifications" in captured["url"]


# ---------------------------------------------------------------------------
# Cycle 6 — n8n failure propagates as is_error
# ---------------------------------------------------------------------------

class TestN8nFailure:
    @pytest.mark.asyncio
    async def test_n8n_down_returns_error_for_list_issues(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin(fetch_fn=_error_fetch())
        plugin = GithubPlugin(n8n_plugin=n8n)
        result = await plugin.call_tool(
            "github_list_issues", {"repo": "x/y"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.github import GithubPlugin
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix GitHub List Issues"))
        plugin = GithubPlugin(n8n_plugin=n8n)
        result = await plugin.call_tool("github_does_not_exist", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — create() factory
# ---------------------------------------------------------------------------

class TestCreate:
    def test_create_accepts_n8n_plugin(self):
        from plugins.github import create
        from plugins.n8n import N8nPlugin

        n8n = N8nPlugin()
        plugin = create(n8n_plugin=n8n)
        assert plugin._n8n is n8n
