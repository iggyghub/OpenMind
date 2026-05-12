"""
Zoom MCP plugin — Issue #23.

Tools: join_meeting, schedule_meeting, list_meetings.

All HTTP work is delegated to an injected N8nPlugin (same delegation pattern
as GoogleWorkspacePlugin in #20). The Zoom desktop client is launched via an
injectable launch_fn so tests don't actually open Zoom.

n8n workflow names that must exist in the local n8n instance:
  "Felix Zoom Join"      join_meeting
  "Felix Zoom Schedule"  schedule_meeting
  "Felix Zoom List"      list_meetings

For join_meeting, the plugin first triggers the n8n workflow (which can log
the join, resolve a meeting ID into a URL, etc.) and then shells out to the
local Zoom client. If n8n fails the launch is skipped and the error is
returned — same fail-loud behaviour as the rest of the plugin pack.
"""
import json
import logging
import webbrowser
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "zoom"

# ADR-0005 / Issue #44 — zoom_join_meeting launches the desktop Zoom client
# via the system URL handler (device_control) and triggers an n8n workflow
# first (network_egress_local + external_data_read for any join-side logging).
# zoom_schedule_meeting writes a new meeting via n8n (external_data_write).
# zoom_list_meetings reads upcoming meetings (external_data_read).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "device_control",
    "external_data_read",
    "external_data_write",
    "network_egress_local",
})

FetchFn = Callable[..., Awaitable[dict]]
LaunchFn = Callable[[str], Any]

_WORKFLOWS = {
    "zoom_join_meeting": "Felix Zoom Join",
    "zoom_schedule_meeting": "Felix Zoom Schedule",
    "zoom_list_meetings": "Felix Zoom List",
}


def _default_launch(url: str) -> None:
    """Open a URL with the system default handler. The `zoommtg://` scheme
    is registered by the Zoom desktop client on install — opening it here
    triggers the same flow as clicking a Zoom link in a browser."""
    webbrowser.open(url)


class ZoomPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        n8n_plugin=None,
        *,
        launch_fn: LaunchFn | None = None,
        fetch_fn: FetchFn | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        if n8n_plugin is not None:
            self._n8n = n8n_plugin
        else:
            from plugins.n8n import N8nPlugin
            self._n8n = N8nPlugin(
                fetch_fn=fetch_fn,
                base_url=base_url,
                api_key=api_key,
            )
        self._launch = launch_fn or _default_launch

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="zoom_join_meeting",
                description=(
                    "Join a Zoom meeting. Provide a full Zoom URL "
                    "(https://zoom.us/j/...) or a numeric meeting ID. "
                    "The local Zoom client is launched automatically."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Full Zoom join URL (preferred)",
                        },
                        "id": {
                            "type": "string",
                            "description": "Numeric Zoom meeting ID (used if no URL)",
                        },
                        "passcode": {
                            "type": "string",
                            "description": "Meeting passcode (optional)",
                        },
                    },
                },
            ),
            Tool(
                name="zoom_schedule_meeting",
                description=(
                    "Schedule a new Zoom meeting at a given start time. "
                    "Returns the join URL once n8n has created it."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Meeting title"},
                        "start": {
                            "type": "string",
                            "description": "Start datetime (ISO 8601)",
                        },
                        "duration_minutes": {
                            "type": "integer",
                            "description": "Meeting length in minutes (default 30)",
                        },
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Email addresses to invite (optional)",
                        },
                    },
                    "required": ["title", "start"],
                },
            ),
            Tool(
                name="zoom_list_meetings",
                description="List upcoming Zoom meetings on the connected account.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of meetings to return (default 10)",
                        },
                    },
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "zoom_join_meeting":
            return await self._join_meeting(args)
        if tool_name == "zoom_schedule_meeting":
            return await self._schedule_meeting(args)
        if tool_name == "zoom_list_meetings":
            return await self._list_meetings(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    async def _join_meeting(self, args: dict) -> ToolResult:
        url = args.get("url")
        meeting_id = args.get("id")
        if not url and not meeting_id:
            return ToolResult(
                content="'url' or 'id' is required for join_meeting",
                is_error=True,
            )

        # Trigger the n8n workflow first so any side effects (logging,
        # resolving an ID into a passworded URL, etc.) run before we open
        # the desktop client. If n8n fails, we don't launch.
        result = await self._n8n.call_tool(
            "trigger_workflow",
            {"name": _WORKFLOWS["zoom_join_meeting"], "data": args},
        )
        if result.is_error:
            return result

        launch_url = url or self._build_zoommtg_url(meeting_id, args.get("passcode"))
        try:
            self._launch(launch_url)
        except Exception as exc:
            logger.error("[zoom] launch_fn failed: %s", exc)
            return ToolResult(
                content=f"Failed to launch Zoom client: {exc}",
                is_error=True,
            )

        return ToolResult(content=json.dumps({"launched": launch_url}))

    async def _schedule_meeting(self, args: dict) -> ToolResult:
        for field in ("title", "start"):
            if not args.get(field):
                return ToolResult(
                    content=f"'{field}' is required for schedule_meeting",
                    is_error=True,
                )
        return await self._n8n.call_tool(
            "trigger_workflow",
            {"name": _WORKFLOWS["zoom_schedule_meeting"], "data": args},
        )

    async def _list_meetings(self, args: dict) -> ToolResult:
        return await self._n8n.call_tool(
            "trigger_workflow",
            {"name": _WORKFLOWS["zoom_list_meetings"], "data": args},
        )

    @staticmethod
    def _build_zoommtg_url(meeting_id: str, passcode: str | None) -> str:
        url = f"zoommtg://zoom.us/join?confno={meeting_id}"
        if passcode:
            url += f"&pwd={passcode}"
        return url


def create(
    n8n_plugin=None,
    *,
    launch_fn: LaunchFn | None = None,
    fetch_fn: FetchFn | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ZoomPlugin:
    return ZoomPlugin(
        n8n_plugin=n8n_plugin,
        launch_fn=launch_fn,
        fetch_fn=fetch_fn,
        base_url=base_url,
        api_key=api_key,
    )
