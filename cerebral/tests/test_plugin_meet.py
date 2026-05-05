"""
Google Meet MCP plugin tests — Issue #23.

TDD vertical slices for MeetPlugin:
  - join_meeting(url)              — opens browser via webbrowser.open (injectable)
  - schedule_meeting(...)          — delegates to GoogleWorkspacePlugin.calendar_create_event
  - get_meeting_link(event_id)     — delegates to GoogleWorkspacePlugin.calendar_list_events
                                      and pulls the Meet URL out of the event payload

The Meet plugin reuses Google Calendar (Meet links live in calendar event
payloads) — no separate Meet API calls. Reuse the GoogleWorkspacePlugin so
we don't duplicate the n8n delegation chain.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeGoogleWorkspace:
    """In-memory stand-in for GoogleWorkspacePlugin used in MeetPlugin tests."""

    name = "google_workspace"

    def __init__(self, *, list_response: list | None = None, raise_on=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._list_response = list_response or []
        self._raise_on = raise_on or set()

    def list_tools(self):
        return []

    async def call_tool(self, tool_name, args):
        from cerebral.mcp.orchestrator import ToolResult

        self.calls.append((tool_name, dict(args)))

        if tool_name in self._raise_on:
            return ToolResult(content="Workspace failure", is_error=True)

        if tool_name == "calendar_create_event":
            return ToolResult(content=json.dumps({
                "event_id": "evt-1",
                "hangoutLink": "https://meet.google.com/aaa-bbbb-ccc",
            }))

        if tool_name == "calendar_list_events":
            return ToolResult(content=json.dumps({"events": self._list_response}))

        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)


# ---------------------------------------------------------------------------
# Cycle 1 — join_meeting opens browser via injected webbrowser_open_fn
# ---------------------------------------------------------------------------

class TestJoinMeeting:
    @pytest.mark.asyncio
    async def test_join_meeting_opens_browser_with_url(self):
        from plugins.meet import MeetPlugin

        opened: list[str] = []
        plugin = MeetPlugin(
            google_workspace_plugin=FakeGoogleWorkspace(),
            webbrowser_open_fn=opened.append,
        )

        url = "https://meet.google.com/abc-defg-hij"
        result = await plugin.call_tool("meet_join_meeting", {"url": url})

        assert not result.is_error
        assert opened == [url]

    @pytest.mark.asyncio
    async def test_join_meeting_missing_url_returns_error(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin(
            google_workspace_plugin=FakeGoogleWorkspace(),
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_join_meeting", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 2 — schedule_meeting delegates to calendar_create_event
# ---------------------------------------------------------------------------

class TestScheduleMeeting:
    @pytest.mark.asyncio
    async def test_schedule_meeting_delegates_to_calendar_create_event(self):
        from plugins.meet import MeetPlugin

        gws = FakeGoogleWorkspace()
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_schedule_meeting", {
            "title": "Design review",
            "start": "2026-05-15T14:00:00",
            "end": "2026-05-15T15:00:00",
            "attendees": ["alice@example.com"],
        })

        assert not result.is_error
        assert len(gws.calls) == 1
        tool, args = gws.calls[0]
        assert tool == "calendar_create_event"
        assert args["title"] == "Design review"
        assert args["start"] == "2026-05-15T14:00:00"
        assert args["end"] == "2026-05-15T15:00:00"
        assert args["attendees"] == ["alice@example.com"]

    @pytest.mark.asyncio
    async def test_schedule_meeting_requests_meet_conference(self):
        """Schedule must signal Calendar to create a Meet conference link.

        The exact key name is implementation-defined but the request must
        pass through SOMETHING that the n8n calendar workflow can use to
        attach a conference."""
        from plugins.meet import MeetPlugin

        gws = FakeGoogleWorkspace()
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        await plugin.call_tool("meet_schedule_meeting", {
            "title": "x",
            "start": "2026-05-15T14:00:00",
        })

        _, args = gws.calls[0]
        # Conference flag must be present and truthy in some recognisable form
        assert any(
            (k.lower() in {"add_conference", "conference", "with_meet", "create_meet"}
             and bool(v))
            for k, v in args.items()
        )

    @pytest.mark.asyncio
    async def test_schedule_meeting_missing_title_returns_error(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin(
            google_workspace_plugin=FakeGoogleWorkspace(),
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_schedule_meeting", {
            "start": "2026-05-15T14:00:00",
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_schedule_meeting_missing_start_returns_error(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin(
            google_workspace_plugin=FakeGoogleWorkspace(),
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_schedule_meeting", {"title": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — get_meeting_link finds Meet URL inside calendar list_events response
# ---------------------------------------------------------------------------

class TestGetMeetingLink:
    @pytest.mark.asyncio
    async def test_get_meeting_link_returns_hangout_link(self):
        from plugins.meet import MeetPlugin

        events = [
            {"id": "evt-other", "hangoutLink": "https://meet.google.com/zzz"},
            {"id": "evt-target", "hangoutLink": "https://meet.google.com/abc-defg-hij"},
        ]
        gws = FakeGoogleWorkspace(list_response=events)
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_get_meeting_link", {"event_id": "evt-target"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["url"] == "https://meet.google.com/abc-defg-hij"

    @pytest.mark.asyncio
    async def test_get_meeting_link_event_not_found_returns_error(self):
        from plugins.meet import MeetPlugin

        events = [{"id": "evt-other", "hangoutLink": "https://meet.google.com/zzz"}]
        gws = FakeGoogleWorkspace(list_response=events)
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_get_meeting_link", {"event_id": "missing"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_get_meeting_link_event_without_meet_returns_error(self):
        from plugins.meet import MeetPlugin

        events = [{"id": "evt-target"}]  # no hangoutLink
        gws = FakeGoogleWorkspace(list_response=events)
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_get_meeting_link", {"event_id": "evt-target"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_get_meeting_link_missing_event_id_returns_error(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin(
            google_workspace_plugin=FakeGoogleWorkspace(),
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_get_meeting_link", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — workspace failure propagates as error
# ---------------------------------------------------------------------------

class TestWorkspaceFailurePropagation:
    @pytest.mark.asyncio
    async def test_schedule_failure_returns_error(self):
        from plugins.meet import MeetPlugin

        gws = FakeGoogleWorkspace(raise_on={"calendar_create_event"})
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_schedule_meeting", {
            "title": "x", "start": "2026-05-15T14:00:00",
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_get_link_failure_returns_error(self):
        from plugins.meet import MeetPlugin

        gws = FakeGoogleWorkspace(raise_on={"calendar_list_events"})
        plugin = MeetPlugin(
            google_workspace_plugin=gws,
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("meet_get_meeting_link", {"event_id": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — unknown tool returns error
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin(
            google_workspace_plugin=FakeGoogleWorkspace(),
            webbrowser_open_fn=lambda _u: None,
        )

        result = await plugin.call_tool("nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — list_tools: 3 tools, correct plugin name
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three_tools(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin()
        names = {t.name for t in plugin.list_tools()}
        assert names == {"meet_join_meeting", "meet_schedule_meeting", "meet_get_meeting_link"}

    def test_list_tools_all_have_correct_plugin_name(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin()
        for tool in plugin.list_tools():
            assert tool.plugin == "meet"

    def test_list_tools_all_have_descriptions_and_schemas(self):
        from plugins.meet import MeetPlugin

        plugin = MeetPlugin()
        for tool in plugin.list_tools():
            assert isinstance(tool.description, str) and tool.description
            assert isinstance(tool.schema, dict) and tool.schema


# ---------------------------------------------------------------------------
# Cycle 7 — create() factory
# ---------------------------------------------------------------------------

class TestCreateFactory:
    def test_create_returns_meet_plugin(self):
        from plugins.meet import create, MeetPlugin

        plugin = create()
        assert isinstance(plugin, MeetPlugin)

    def test_create_plugin_name_is_meet(self):
        from plugins.meet import create

        assert create().name == "meet"

    def test_create_accepts_google_workspace_plugin(self):
        from plugins.meet import create

        gws = FakeGoogleWorkspace()
        plugin = create(google_workspace_plugin=gws)
        assert plugin._gws is gws

    def test_create_accepts_webbrowser_open_fn(self):
        from plugins.meet import create

        sentinel = lambda _u: None  # noqa: E731
        plugin = create(webbrowser_open_fn=sentinel)
        assert plugin._open is sentinel
