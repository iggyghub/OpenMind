"""
Clock plugin — MCP server for Felix.

Tools: get_time, set_alarm (stub), set_timer, set_reminder, list_timers.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "clock"

# ADR-0005 / Issue #44 — set_timer / set_reminder fire OS notifications via
# the injected notify_fn (device_control). get_time / list_timers are pure
# local introspection but the plugin's overall surface is device-level.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"device_control"})

# Monotonically increasing timer id counter (per process)
_NEXT_ID = 1


def _default_notify(title: str, message: str) -> None:
    logger.info("[clock] notify: %s — %s", title, message)


class ClockPlugin:
    name = PLUGIN_NAME

    def __init__(self, notify_fn=None):
        self._notify_fn = notify_fn or _default_notify
        # id → {"label": str, "task": asyncio.Task}
        self._timers: dict[int, dict] = {}

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_time",
                description="Returns the current local time, or time in a named timezone.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "IANA timezone name, e.g. 'Europe/London'. Omit for local time.",
                        }
                    },
                },
            ),
            Tool(
                name="set_alarm",
                description="Sets a one-shot alarm at the given time (stub — not yet wired to scheduler).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "time": {"type": "string", "description": "HH:MM in 24-hour format"},
                        "label": {"type": "string", "description": "Optional alarm label"},
                    },
                    "required": ["time"],
                },
            ),
            Tool(
                name="set_timer",
                description="Fires an OS notification after a given number of seconds.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "seconds": {"type": "number", "description": "How many seconds until the timer fires"},
                        "label": {"type": "string", "description": "What this timer is for"},
                    },
                    "required": ["seconds"],
                },
            ),
            Tool(
                name="set_reminder",
                description="Queues a reminder that fires an OS notification after a delay.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Reminder text to show"},
                        "delay_seconds": {"type": "number", "description": "Seconds from now to fire"},
                    },
                    "required": ["text", "delay_seconds"],
                },
            ),
            Tool(
                name="list_timers",
                description="Returns all currently active (pending) timers and reminders.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "get_time":
            return await self._get_time(args)
        if tool_name == "set_alarm":
            return self._set_alarm(args)
        if tool_name == "set_timer":
            return await self._set_timer(args.get("seconds", 0), args.get("label", "Timer"))
        if tool_name == "set_reminder":
            return await self._set_timer(args.get("delay_seconds", 0), args.get("text", "Reminder"))
        if tool_name == "list_timers":
            return self._list_timers()
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_time(self, args: dict) -> ToolResult:
        tz_name = args.get("timezone")
        try:
            tz = ZoneInfo(tz_name) if tz_name else None
        except ZoneInfoNotFoundError:
            return ToolResult(content=f"Unknown timezone: '{tz_name}'", is_error=True)
        now = datetime.now(tz=tz or timezone.utc).astimezone(tz)
        return ToolResult(content=now.strftime("%H:%M:%S %Z"))

    def _set_alarm(self, args: dict) -> ToolResult:
        time_str = args.get("time", "")
        label = args.get("label", "Alarm")
        return ToolResult(content=f"Alarm '{label}' set for {time_str} (scheduler not yet wired)")

    async def _set_timer(self, seconds: float, label: str) -> ToolResult:
        global _NEXT_ID
        timer_id = _NEXT_ID
        _NEXT_ID += 1

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — stub gracefully
            logger.warning("[clock] No running event loop; timer '%s' not scheduled", label)
            return ToolResult(content=json.dumps({"id": timer_id, "label": label, "seconds": seconds}))

        task = loop.create_task(self._timer_task(timer_id, seconds, label))
        self._timers[timer_id] = {"label": label, "seconds": seconds, "task": task}
        return ToolResult(content=json.dumps({"id": timer_id, "label": label, "seconds": seconds}))

    async def _timer_task(self, timer_id: int, seconds: float, label: str) -> None:
        try:
            await asyncio.sleep(seconds)
            self._notify_fn("Felix — Timer", f"{label} is done")
        except asyncio.CancelledError:
            pass
        finally:
            self._timers.pop(timer_id, None)

    def _list_timers(self) -> ToolResult:
        timers = [
            {"id": tid, "label": info["label"], "seconds": info["seconds"]}
            for tid, info in self._timers.items()
            if not info["task"].done()
        ]
        return ToolResult(content=json.dumps({"timers": timers}))

    def cancel_all(self) -> None:
        """Cancel all pending timers — used in tests and on shutdown."""
        for info in list(self._timers.values()):
            info["task"].cancel()
        self._timers.clear()


def create() -> ClockPlugin:
    return ClockPlugin()
