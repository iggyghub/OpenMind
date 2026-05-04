"""
Google Workspace MCP plugin — Issue #20.

Exposes higher-level Google tools by triggering pre-built n8n workflows.
Delegates ALL HTTP to an injected N8nPlugin — no direct Google API calls.

Delegation chain:
  GoogleWorkspacePlugin.call_tool(tool, args)
    → N8nPlugin.call_tool("trigger_workflow", {"name": WORKFLOW, "data": payload})
      → n8n webhook → Google API

n8n workflow names that must exist in the local n8n instance:
  "Felix Gmail Send"         gmail_send
  "Felix Gmail Search"       gmail_search
  "Felix Calendar Create"    calendar_create_event
  "Felix Calendar List"      calendar_list_events
  "Felix Drive List Files"   drive_list_files
  "Felix Drive Upload"       drive_upload_file
  "Felix Sheets Read"        sheets_read_range
  "Felix Sheets Write"       sheets_write_range

Intentionally omitted (scope): Docs, Slides, Contacts, Maps, Tasks.
These follow the same delegation pattern and can be added via the growth loop
when needed.

All deps are injectable; fetch_fn flows through to the N8nPlugin so tests run
without a live n8n or Google account.
"""
import logging
import os
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_workspace"

FetchFn = Callable[..., Awaitable[dict]]

# Workflow name registry — one place to update if workflow names change in n8n
_WORKFLOWS = {
    "gmail_send": "Felix Gmail Send",
    "gmail_search": "Felix Gmail Search",
    "calendar_create_event": "Felix Calendar Create",
    "calendar_list_events": "Felix Calendar List",
    "drive_list_files": "Felix Drive List Files",
    "drive_upload_file": "Felix Drive Upload",
    "sheets_read_range": "Felix Sheets Read",
    "sheets_write_range": "Felix Sheets Write",
}

_REQUIRED: dict[str, list[str]] = {
    "gmail_send": ["to", "subject", "body"],
    "gmail_search": ["query"],
    "calendar_create_event": ["title", "start"],
    "calendar_list_events": [],
    "drive_list_files": [],
    "drive_upload_file": ["filename", "content"],
    "sheets_read_range": ["spreadsheet_id", "range"],
    "sheets_write_range": ["spreadsheet_id", "range", "data"],
}


class GoogleWorkspacePlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        n8n_plugin=None,
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

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="gmail_send",
                description="Send an email via Gmail. Requires: to, subject, body.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject line"},
                        "body": {"type": "string", "description": "Email body (plain text)"},
                        "cc": {"type": "string", "description": "CC email address (optional)"},
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            Tool(
                name="gmail_search",
                description=(
                    "Search Gmail using a Gmail query string "
                    "(e.g. 'from:alice subject:report is:unread')."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Gmail search query"},
                        "max_results": {"type": "integer", "description": "Max messages to return (default 10)"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="calendar_create_event",
                description="Create a Google Calendar event.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Event title"},
                        "start": {"type": "string", "description": "Start datetime (ISO 8601)"},
                        "end": {"type": "string", "description": "End datetime (ISO 8601, optional)"},
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of attendee email addresses (optional)",
                        },
                        "description": {"type": "string", "description": "Event description (optional)"},
                    },
                    "required": ["title", "start"],
                },
            ),
            Tool(
                name="calendar_list_events",
                description="List upcoming Google Calendar events, optionally filtered by date range.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "from": {"type": "string", "description": "Start date filter (ISO 8601, optional)"},
                        "to": {"type": "string", "description": "End date filter (ISO 8601, optional)"},
                        "max_results": {"type": "integer", "description": "Max events to return (default 10)"},
                    },
                },
            ),
            Tool(
                name="drive_list_files",
                description="List files in Google Drive, optionally filtered by query or folder.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Drive search query (optional)"},
                        "folder_id": {"type": "string", "description": "Restrict to this folder ID (optional)"},
                        "max_results": {"type": "integer", "description": "Max files to return (default 20)"},
                    },
                },
            ),
            Tool(
                name="drive_upload_file",
                description="Upload a file to Google Drive.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Name for the uploaded file"},
                        "content": {"type": "string", "description": "File content (text or base64-encoded)"},
                        "folder_id": {"type": "string", "description": "Target folder ID (optional)"},
                        "mime_type": {"type": "string", "description": "MIME type (optional, n8n auto-detects)"},
                    },
                    "required": ["filename", "content"],
                },
            ),
            Tool(
                name="sheets_read_range",
                description="Read a cell range from a Google Sheet.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID"},
                        "range": {"type": "string", "description": "A1 notation range, e.g. 'Sheet1!A1:D10'"},
                    },
                    "required": ["spreadsheet_id", "range"],
                },
            ),
            Tool(
                name="sheets_write_range",
                description="Write rows to a Google Sheet starting at the given cell range.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID"},
                        "range": {"type": "string", "description": "Top-left cell in A1 notation, e.g. 'Sheet1!A1'"},
                        "data": {
                            "type": "array",
                            "items": {"type": "array"},
                            "description": "2D array of rows to write",
                        },
                    },
                    "required": ["spreadsheet_id", "range", "data"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name not in _WORKFLOWS:
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

        required = _REQUIRED.get(tool_name, [])
        for field in required:
            if not args.get(field):
                return ToolResult(
                    content=f"'{field}' is required for {tool_name}",
                    is_error=True,
                )

        workflow_name = _WORKFLOWS[tool_name]
        return await self._n8n.call_tool(
            "trigger_workflow",
            {"name": workflow_name, "data": args},
        )


def create(
    n8n_plugin=None,
    fetch_fn: FetchFn | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> GoogleWorkspacePlugin:
    return GoogleWorkspacePlugin(
        n8n_plugin=n8n_plugin,
        fetch_fn=fetch_fn,
        base_url=base_url,
        api_key=api_key,
    )
