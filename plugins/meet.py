"""
Google Meet MCP plugin — Issue #23.

Tools: join_meeting, schedule_meeting, get_meeting_link.

Meet links live on Google Calendar events, so this plugin reuses the
GoogleWorkspacePlugin (#20) instead of duplicating the n8n delegation
chain. The browser launch for join_meeting is injectable so tests don't
open a real browser tab.

Tool routing:
  join_meeting(url)        → webbrowser.open(url)        (no n8n)
  schedule_meeting(...)    → GoogleWorkspacePlugin.calendar_create_event
                              with `add_conference=True` so the n8n
                              calendar workflow attaches a Meet link
  get_meeting_link(event)  → GoogleWorkspacePlugin.calendar_list_events
                              and pull `hangoutLink` from the matching event
"""
import json
import logging
import webbrowser
from typing import Any, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "meet"

# ADR-0005 / Issue #44 — meet_join_meeting opens a browser tab
# (device_control); meet_schedule_meeting creates a calendar event with a
# Meet link (external_data_write via the gws plugin → local n8n);
# meet_get_meeting_link reads a calendar event (external_data_read).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "device_control",
    "external_data_read",
    "external_data_write",
    "network_egress_local",
})

OpenFn = Callable[[str], Any]


def _default_open(url: str) -> None:
    webbrowser.open(url)


class MeetPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        google_workspace_plugin=None,
        *,
        webbrowser_open_fn: OpenFn | None = None,
        n8n_plugin=None,
        fetch_fn=None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        if google_workspace_plugin is not None:
            self._gws = google_workspace_plugin
        else:
            try:
                from plugins.google_workspace import GoogleWorkspacePlugin
                self._gws = GoogleWorkspacePlugin(
                    n8n_plugin=n8n_plugin,
                    fetch_fn=fetch_fn,
                    base_url=base_url,
                    api_key=api_key,
                )
            except ModuleNotFoundError:
                from plugins.calendar import create as create_calendar
                self._gws = create_calendar()
        self._open = webbrowser_open_fn or _default_open

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="meet_join_meeting",
                description=(
                    "Join a Google Meet by URL. Opens the meeting in the "
                    "default browser — Meet runs in-browser so no client "
                    "install is required."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Full Meet URL (https://meet.google.com/...)",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="meet_schedule_meeting",
                description=(
                    "Schedule a Google Meet by creating a calendar event "
                    "with a Meet conference attached."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Event title"},
                        "start": {
                            "type": "string",
                            "description": "Start datetime (ISO 8601)",
                        },
                        "end": {
                            "type": "string",
                            "description": "End datetime (ISO 8601, optional)",
                        },
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Email addresses to invite (optional)",
                        },
                        "description": {
                            "type": "string",
                            "description": "Event description (optional)",
                        },
                    },
                    "required": ["title", "start"],
                },
            ),
            Tool(
                name="meet_get_meeting_link",
                description=(
                    "Look up the Google Meet URL attached to an existing "
                    "calendar event by its event id."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "Google Calendar event id",
                        },
                    },
                    "required": ["event_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "meet_join_meeting":
            return await self._join_meeting(args)
        if tool_name == "meet_schedule_meeting":
            return await self._schedule_meeting(args)
        if tool_name == "meet_get_meeting_link":
            return await self._get_meeting_link(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    async def _join_meeting(self, args: dict) -> ToolResult:
        url = args.get("url")
        if not url:
            return ToolResult(
                content="'url' is required for join_meeting",
                is_error=True,
            )
        try:
            self._open(url)
        except Exception as exc:
            logger.error("[meet] browser open failed: %s", exc)
            return ToolResult(
                content=f"Failed to open browser: {exc}",
                is_error=True,
            )
        return ToolResult(content=json.dumps({"opened": url}))

    async def _schedule_meeting(self, args: dict) -> ToolResult:
        for field in ("title", "start"):
            if not args.get(field):
                return ToolResult(
                    content=f"'{field}' is required for schedule_meeting",
                    is_error=True,
                )

        # Forward to calendar_create_event with the conference flag set so
        # the n8n calendar workflow attaches a Meet link to the event.
        payload = {**args, "add_conference": True}
        return await self._gws.call_tool("calendar_create_event", payload)

    async def _get_meeting_link(self, args: dict) -> ToolResult:
        event_id = args.get("event_id")
        if not event_id:
            return ToolResult(
                content="'event_id' is required for get_meeting_link",
                is_error=True,
            )

        result = await self._gws.call_tool("calendar_list_events", {})
        if result.is_error:
            return result

        try:
            data = json.loads(result.content)
        except (ValueError, TypeError) as exc:
            return ToolResult(
                content=f"Failed to parse calendar response: {exc}",
                is_error=True,
            )

        events = data.get("events", []) if isinstance(data, dict) else []
        match = next((e for e in events if e.get("id") == event_id), None)
        if match is None:
            return ToolResult(
                content=f"Event not found: '{event_id}'",
                is_error=True,
            )

        link = match.get("hangoutLink") or match.get("meet_url")
        if not link:
            return ToolResult(
                content=f"Event '{event_id}' has no Meet link attached",
                is_error=True,
            )

        return ToolResult(content=json.dumps({"event_id": event_id, "url": link}))


def create(
    google_workspace_plugin=None,
    *,
    webbrowser_open_fn: OpenFn | None = None,
    n8n_plugin=None,
    fetch_fn=None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> MeetPlugin:
    return MeetPlugin(
        google_workspace_plugin=google_workspace_plugin,
        webbrowser_open_fn=webbrowser_open_fn,
        n8n_plugin=n8n_plugin,
        fetch_fn=fetch_fn,
        base_url=base_url,
        api_key=api_key,
    )
