"""
Zoom MCP plugin tests — Issue #23.

TDD vertical slices for ZoomPlugin:
  - join_meeting(url=..., id=...)   — triggers 'Felix Zoom Join' via n8n
                                       and shells out to the Zoom client locally
  - schedule_meeting(title, start)  — triggers 'Felix Zoom Schedule' via n8n
  - list_meetings()                 — triggers 'Felix Zoom List' via n8n

All HTTP calls go through an injected N8nPlugin and the desktop launch
is injected via `launch_fn` so no live n8n and no Zoom client is needed.

Delegation chain:
  ZoomPlugin.call_tool(tool, args)
    → N8nPlugin.call_tool("trigger_workflow", {"name": WORKFLOW, "data": payload})
      → fake_fetch (injected at test time)
"""
import json
import sys
from pathlib import Path

import pytest

# Ensure plugins/ is importable from anywhere in the tree
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers — build a fake_fetch that mimics n8n for one workflow
# ---------------------------------------------------------------------------

def _make_fetch(workflow_name: str, *, captured: dict | None = None,
                meetings: list | None = None):
    """Mimics n8n: GET /api/v1/workflows returns the named workflow,
    POST /webhook/<name> returns either an executionId or a meetings list."""
    async def fake_fetch(method, url, *, headers=None, json=None):
        if method == "GET" and "/api/v1/workflows" in url:
            return {"data": [{"id": "w-zoom", "name": workflow_name, "active": True}]}
        if method == "POST":
            if captured is not None:
                captured.update(json or {})
            if meetings is not None:
                return {"executionId": "exec-1", "meetings": meetings}
            return {"executionId": "exec-1"}
        return {}
    return fake_fetch


def _make_error_fetch():
    async def fake_fetch(method, url, *, headers=None, json=None):
        raise ConnectionError("n8n unreachable")
    return fake_fetch


# ---------------------------------------------------------------------------
# Cycle 1 — join_meeting: requires url or id
# ---------------------------------------------------------------------------

class TestJoinMeetingRequiredArgs:
    @pytest.mark.asyncio
    async def test_join_meeting_missing_url_and_id_returns_error(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Join"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("zoom_join_meeting", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 2 — join_meeting with URL: launches Zoom client + triggers n8n
# ---------------------------------------------------------------------------

class TestJoinMeetingByUrl:
    @pytest.mark.asyncio
    async def test_join_meeting_by_url_calls_launch_fn(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        launched: list[str] = []
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Join"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=launched.append)

        url = "https://zoom.us/j/1234567890"
        result = await plugin.call_tool("zoom_join_meeting", {"url": url})

        assert not result.is_error
        assert launched == [url]

    @pytest.mark.asyncio
    async def test_join_meeting_triggers_zoom_join_workflow(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Join", captured=captured))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        url = "https://zoom.us/j/9999"
        await plugin.call_tool("zoom_join_meeting", {"url": url})

        # n8n receives the URL in the workflow trigger payload
        assert captured.get("url") == url


# ---------------------------------------------------------------------------
# Cycle 3 — join_meeting with ID only: builds zoommtg:// URL
# ---------------------------------------------------------------------------

class TestJoinMeetingById:
    @pytest.mark.asyncio
    async def test_join_meeting_by_id_builds_zoommtg_url(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        launched: list[str] = []
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Join"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=launched.append)

        await plugin.call_tool("zoom_join_meeting", {"id": "1234567890"})

        assert len(launched) == 1
        assert "1234567890" in launched[0]
        # zoommtg scheme so the desktop client (not browser) opens it
        assert launched[0].startswith("zoommtg://")

    @pytest.mark.asyncio
    async def test_join_meeting_by_id_passes_id_to_n8n(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Join", captured=captured))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        await plugin.call_tool("zoom_join_meeting", {"id": "5550001234"})

        assert captured.get("id") == "5550001234"


# ---------------------------------------------------------------------------
# Cycle 4 — schedule_meeting: requires title + start
# ---------------------------------------------------------------------------

class TestScheduleMeeting:
    @pytest.mark.asyncio
    async def test_schedule_meeting_calls_correct_workflow(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Schedule"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("zoom_schedule_meeting", {
            "title": "Project sync",
            "start": "2026-05-12T14:00:00",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_schedule_meeting_payload_fields(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Schedule", captured=captured))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        await plugin.call_tool("zoom_schedule_meeting", {
            "title": "Sprint review",
            "start": "2026-05-15T10:00:00",
            "duration_minutes": 45,
            "attendees": ["alice@example.com", "bob@example.com"],
        })

        assert captured.get("title") == "Sprint review"
        assert captured.get("start") == "2026-05-15T10:00:00"
        assert captured.get("duration_minutes") == 45
        assert captured.get("attendees") == ["alice@example.com", "bob@example.com"]

    @pytest.mark.asyncio
    async def test_schedule_meeting_missing_title_returns_error(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Schedule"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("zoom_schedule_meeting", {"start": "2026-05-15T10:00:00"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_schedule_meeting_missing_start_returns_error(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Schedule"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("zoom_schedule_meeting", {"title": "X"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — list_meetings: triggers Felix Zoom List
# ---------------------------------------------------------------------------

class TestListMeetings:
    @pytest.mark.asyncio
    async def test_list_meetings_calls_correct_workflow(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom List"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("zoom_list_meetings", {})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_list_meetings_passes_optional_filters(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom List", captured=captured))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        await plugin.call_tool("zoom_list_meetings", {"max_results": 25})
        assert captured.get("max_results") == 25


# ---------------------------------------------------------------------------
# Cycle 6 — n8n failure propagates as error and Zoom is not launched
# ---------------------------------------------------------------------------

class TestN8nFailurePropagation:
    @pytest.mark.asyncio
    async def test_n8n_down_returns_error_for_join(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        launched: list[str] = []
        n8n = N8nPlugin(fetch_fn=_make_error_fetch())
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=launched.append)

        result = await plugin.call_tool("zoom_join_meeting", {"url": "https://zoom.us/j/1"})
        assert result.is_error
        # If n8n fails, do not launch the desktop client
        assert launched == []

    @pytest.mark.asyncio
    async def test_n8n_down_returns_error_for_schedule(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_error_fetch())
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("zoom_schedule_meeting", {
            "title": "x", "start": "2026-05-12T14:00:00",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — unknown tool returns error
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import ZoomPlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Zoom Join"))
        plugin = ZoomPlugin(n8n_plugin=n8n, launch_fn=lambda _u: None)

        result = await plugin.call_tool("does_not_exist", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — list_tools: 3 tools, correct plugin name, all schemas present
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three_tools(self):
        from plugins.zoom import ZoomPlugin

        plugin = ZoomPlugin()
        names = {t.name for t in plugin.list_tools()}
        assert names == {"zoom_join_meeting", "zoom_schedule_meeting", "zoom_list_meetings"}

    def test_list_tools_all_have_correct_plugin_name(self):
        from plugins.zoom import ZoomPlugin

        plugin = ZoomPlugin()
        for tool in plugin.list_tools():
            assert tool.plugin == "zoom"

    def test_list_tools_all_have_descriptions_and_schemas(self):
        from plugins.zoom import ZoomPlugin

        plugin = ZoomPlugin()
        for tool in plugin.list_tools():
            assert isinstance(tool.description, str) and tool.description
            assert isinstance(tool.schema, dict) and tool.schema


# ---------------------------------------------------------------------------
# Cycle 9 — create() factory
# ---------------------------------------------------------------------------

class TestCreateFactory:
    def test_create_returns_zoom_plugin(self):
        from plugins.zoom import create, ZoomPlugin

        plugin = create()
        assert isinstance(plugin, ZoomPlugin)

    def test_create_plugin_name_is_zoom(self):
        from plugins.zoom import create

        assert create().name == "zoom"

    def test_create_accepts_n8n_plugin(self):
        from plugins.n8n import N8nPlugin
        from plugins.zoom import create

        n8n = N8nPlugin()
        plugin = create(n8n_plugin=n8n)
        assert plugin._n8n is n8n

    def test_create_accepts_launch_fn(self):
        from plugins.zoom import create

        sentinel = lambda _u: None  # noqa: E731
        plugin = create(launch_fn=sentinel)
        assert plugin._launch is sentinel
