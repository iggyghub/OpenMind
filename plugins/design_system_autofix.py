"""Design-system autofix plugin -- MCP tools for scanning and toggling
autonomous UI-rule enforcement. Extracted from plugins/scheduler.py per
SCHEDULER-SPLIT.md S1 (#1209).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from cerebral import design_system as _design_system
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.settings import SettingsStore

logger = logging.getLogger(__name__)

PLUGIN_NAME = "design_system_autofix"

# fs_read: scan reads tray/ files; fs_write: start/stop_design_system_autofix
# mutates the settings store (felix-settings.json).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})


class DesignSystemAutofixPlugin:
    name = PLUGIN_NAME
    DESIGN_SYSTEM_EVENT_TITLE = "__base_design_system_scan__"

    def __init__(self, settings=None, scheduler=None):
        # scheduler: the live SchedulerPlugin instance whose events table
        # ensure_design_system_event() writes to (the table stays there per
        # SCHEDULER-SPLIT.md SAFETY).
        self._settings = settings if settings is not None else SettingsStore()
        self._scheduler = scheduler

    def list_tools(self):
        return [
            Tool(
                name="scan_design_system",
                description=(
                    "Read-only: scan tray/ against BASE-DESIGN-SYSTEM.md's baseline UI "
                    "rules (currently: every tab bar must use tray/lib/tab-strip.js's "
                    "scroll+reorder behavior) and return any gaps found. Safe to call any "
                    "time -- never files an issue or edits code by itself; that only "
                    "happens on the recurring scan's own tick, and only when "
                    "design_system_autofix_enabled is on."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="start_design_system_autofix",
                description=(
                    "Enable the standing base-design-system loop: its daily scan starts "
                    "filing a GitHub issue + queuing a self_dev_campaign slice "
                    "(BASE-DESIGN-SYSTEM.md) for each new gap it finds, instead of only "
                    "logging it. Default OFF, mirroring discovery_enabled's own precedent "
                    "-- the scan itself always runs, this only gates the autonomous "
                    "issue-filing + code-editing side effects."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="stop_design_system_autofix",
                description="Disable the base-design-system loop's autofix side effects (issue filing + self_dev_campaign). The scan itself keeps running and logging.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "scan_design_system":
            return self._scan_design_system(args)
        if tool_name == "start_design_system_autofix":
            return self._start_design_system_autofix(args)
        if tool_name == "stop_design_system_autofix":
            return self._stop_design_system_autofix(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    def _scan_design_system(self, args: dict) -> ToolResult:  # noqa: ARG002
        tray_dir = Path(__file__).resolve().parent.parent / "tray"
        violations = _design_system.scan(tray_dir)
        return ToolResult(content=json.dumps({
            "violations": [
                {"rule_id": v.rule_id, "file": v.file, "line": v.line,
                 "snippet": v.snippet, "description": v.description}
                for v in violations
            ],
        }))

    def _start_design_system_autofix(self, args: dict) -> ToolResult:  # noqa: ARG002
        self._settings.set("design_system_autofix_enabled", True)
        return ToolResult(content=json.dumps({"enabled": True}))

    def _stop_design_system_autofix(self, args: dict) -> ToolResult:  # noqa: ARG002
        self._settings.set("design_system_autofix_enabled", False)
        return ToolResult(content=json.dumps({"enabled": False}))

    def ensure_design_system_event(self, recurrence: str = "24h") -> None:
        """Idempotent get-or-create for the daily base-design-system scan event.
        Delegates to the scheduler's events table (stays on SchedulerPlugin per
        SCHEDULER-SPLIT.md SAFETY; this plugin just owns the title constant)."""
        sched = self._scheduler
        if sched is None:
            logger.warning("[design_system_autofix] ensure_design_system_event called with no scheduler attached")
            return
        existing = sched._con.execute(
            "SELECT id FROM events WHERE title = ?", (self.DESIGN_SYSTEM_EVENT_TITLE,)
        ).fetchone()
        if existing is not None:
            return
        sched._create_event({
            "title": self.DESIGN_SYSTEM_EVENT_TITLE,
            "start_iso": datetime.now(timezone.utc).isoformat(),
            "recurrence": recurrence,
        })


def create() -> DesignSystemAutofixPlugin:
    return DesignSystemAutofixPlugin()
