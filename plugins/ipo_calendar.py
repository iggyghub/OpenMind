"""IPO calendar plugin -- MCP tools for the autonomous IPO-tracking loop.
Extracted from plugins/scheduler.py per SCHEDULER-SPLIT.md S4 (#1212).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.settings import SettingsStore
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "ipo_calendar"

_DEFAULT_DB = data_dir() / "openmind.db"

# fs_read: check_ipo_calendar reads ipo_tracked from settings;
# fs_write: both tools mutate settings.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})


class IpoCalendarPlugin:
    name = PLUGIN_NAME

    # The recurring event title that means "run the IPO-calendar refresh".
    # Stays here (not on SchedulerPlugin) per SCHEDULER-SPLIT.md S4 (#1212).
    IPO_CALENDAR_EVENT_TITLE = "__ipo_calendar_check__"

    def __init__(self, db_path=None, router=None, record_activity_fn=None,
                 settings=None, scheduler=None):
        # scheduler: the live SchedulerPlugin instance whose events table
        # ensure_ipo_calendar_event() writes to.
        self._scheduler = scheduler
        self._router = router
        self._record_activity_fn = record_activity_fn

        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        if settings is not None:
            self._settings = settings
        elif path == ":memory:":
            self._settings = SettingsStore(path=Path(":memory:"))
        else:
            self._settings = SettingsStore(path=Path(path).parent / "felix-settings.json")

    def list_tools(self):
        return [
            Tool(
                name="check_ipo_calendar",
                description="Checks the IPO calendar for upcoming IPOs and tracks new ones.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="dispatch_due_ipos",
                description="Dispatches strategies for any IPOs whose date is today or in the past.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "check_ipo_calendar":
            return await self._check_ipo_calendar(args)
        if tool_name == "dispatch_due_ipos":
            return await self._dispatch_due_ipos(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def ensure_ipo_calendar_event(self, recurrence: str = "7d") -> None:
        """Idempotent get-or-create for the weekly IPO-calendar-refresh event.
        Delegates to the scheduler's events table (stays on SchedulerPlugin per
        SCHEDULER-SPLIT.md SAFETY; this plugin just owns the title constant)."""
        sched = self._scheduler
        if sched is None:
            logger.warning("[ipo_calendar] ensure_ipo_calendar_event called with no scheduler attached")
            return
        existing = sched._con.execute(
            "SELECT id FROM events WHERE title = ?", (self.IPO_CALENDAR_EVENT_TITLE,)
        ).fetchone()
        if existing is not None:
            return
        sched._create_event({
            "title": self.IPO_CALENDAR_EVENT_TITLE,
            "start_iso": datetime.now(timezone.utc).isoformat(),
            "recurrence": recurrence,
        })

    async def _check_ipo_calendar(self, args: dict) -> ToolResult:
        from cerebral.trading.ipo_calendar import fetch_upcoming_ipos
        try:
            upcoming = fetch_upcoming_ipos()
        except Exception as exc:
            return ToolResult(content=f"IPO calendar fetch failed: {exc}", is_error=True)

        tracked = self._settings.get("ipo_tracked") or []
        known_tickers = {t["ticker"] for t in tracked}
        added = []
        for ipo in upcoming:
            if ipo["ticker"] in known_tickers:
                continue
            entry = {**ipo, "dispatched": False}
            tracked.append(entry)
            added.append(entry)
        self._settings.set("ipo_tracked", tracked)
        if added and self._record_activity_fn is not None:
            await self._record_activity_fn("activity", {
                "source": "trading",
                "summary": f"IPO calendar: tracking {len(added)} new upcoming IPO(s): "
                           + ", ".join(f"{e['ticker']} ({e['ipo_date']})" for e in added),
            })
        return ToolResult(content=json.dumps({"tracked_total": len(tracked), "added": added}))

    async def _dispatch_due_ipos(self, args: dict) -> ToolResult:
        from datetime import date
        from cerebral.trading.ipo_strategy import ipo_play_code
        from cerebral.trading.strategy_store import StrategyStore, StrategySpec

        tracked = self._settings.get("ipo_tracked") or []
        today = date.today().isoformat()
        store = StrategyStore()
        dispatched = []
        for entry in tracked:
            if entry.get("dispatched") or entry["ipo_date"] > today:
                continue
            strategy_id = f"IPO play: {entry['ticker']} ({entry['company']})"
            spec = StrategySpec(
                strategy_id=strategy_id, symbol=entry["ticker"],
                code=ipo_play_code(entry["ipo_date"]), qty=1.0, interval="5m",
                risk_override_pct=25.0,
            )
            store.save(spec, origin="discovered", hypothesis=f"IPO pop-then-fade play on {entry['ticker']}",
                       provenance_json={"source": f"ipo_calendar: {entry['ticker']} IPO {entry['ipo_date']}"})
            # No Gauntlet pass means no Gauntlet-created event -- and live_tick only runs a
            # strategy with a due event titled by its id (#1351). Same as the trend basket.
            sched = self._scheduler
            if sched is not None and sched._con.execute(
                    "SELECT id FROM events WHERE title = ?", (strategy_id,)).fetchone() is None:
                sched._create_event({"title": strategy_id, "recurrence": "5m",
                                     "start_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")})
            entry["dispatched"] = True
            dispatched.append(entry["ticker"])
        if dispatched:
            self._settings.set("ipo_tracked", tracked)
            if self._record_activity_fn is not None:
                await self._record_activity_fn("activity", {
                    "source": "trading",
                    "summary": f"IPO strategy registered and trading at today's open: {', '.join(dispatched)}",
                })
        return ToolResult(content=json.dumps({"dispatched": dispatched}))


def create() -> IpoCalendarPlugin:
    return IpoCalendarPlugin()
