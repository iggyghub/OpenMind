"""
Scheduler plugin — MCP server for Felix.

Tools: create_event, list_events, update_event, delete_event, run_gauntlet.
SQLite-backed (same openmind.db). No external calendar deps.
"""
import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading.live_tick import run_strategy_tick
from cerebral.trading.strategy_store import StrategySpec, StrategyStore, mint_expansion_strategy_id
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.gauntlet import run_gauntlet, compute_max_holding_days
from cerebral.trading.replay import run_bars
from cerebral.trading.discovery import (
    build_dynamic_universe,
    rank_for_day_trading,
)
from cerebral.settings import SettingsStore

logger = logging.getLogger(__name__)

PLUGIN_NAME = "scheduler"

# ADR-0005 / Issue #44 — list_events reads SQLite (fs_read); create_event /
# update_event / delete_event mutate the events table (fs_write).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})

from cerebral.paths import data_dir

_DEFAULT_DB = data_dir() / "openmind.db"

_VALID_RECURRENCES = {"daily", "weekly", "monthly"}
# S7-S9: short intraday intervals for the autonomous paper-trade dispatcher
# (gauntlet.py schedules "5m") -- a separate pattern from the calendar-style
# literals above rather than folding "5m" into _VALID_RECURRENCES, since it's
# a different axis (a duration, not a named cadence).
_SHORT_RECURRENCE_RE = re.compile(r"^(\d+)(m|h)$")


def _is_valid_recurrence(recurrence: str) -> bool:
    return recurrence in _VALID_RECURRENCES or bool(_SHORT_RECURRENCE_RE.match(recurrence))


def _recurrence_interval(recurrence: str | None) -> "timedelta | None":
    """Time between recurrences, or None for a one-time event / unknown value."""
    if not recurrence:
        return None
    if recurrence == "daily":
        return timedelta(days=1)
    if recurrence == "weekly":
        return timedelta(weeks=1)
    if recurrence == "monthly":
        return timedelta(days=30)  # approximate -- fine for a due-check, not billing
    m = _SHORT_RECURRENCE_RE.match(recurrence)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return timedelta(minutes=n) if unit == "m" else timedelta(hours=n)
    return None


def _parse_iso(s: "str | None") -> "datetime | None":
    """Always returns a naive UTC datetime, even when `s` carries an
    explicit offset (e.g. `datetime.now(timezone.utc).isoformat()`, as
    ensure_discovery_event's start_iso does). list_due_events compares
    everything against `now = datetime.now(timezone.utc).replace(tzinfo=
    None)` and mark_event_run's own last_run_iso (stored via strftime,
    never carries an offset) -- an aware value straight out of
    fromisoformat crashed that comparison with "can't compare
    offset-naive and offset-aware datetimes" (found live 2026-08-25: the
    discovery event's own start_iso is the one production value that
    hits this path, so the autonomous scheduler loop never actually
    fired it -- only got this far after fixing the last_run_iso column
    migration above)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class SchedulerPlugin:
    name = PLUGIN_NAME

    IPO_CALENDAR_EVENT_TITLE = "__ipo_calendar_check__"

    def __init__(self, db_path=None, router=None, record_activity_fn=None,
                 settings=None):
        self._router = router
        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()
        self._record_activity_fn = record_activity_fn
        # S31 (#896): same isolation convention -- a tmp_path-scoped
        # SchedulerPlugin(db_path=...) test gets its own felix-settings.json
        # instead of silently touching the real one.
        if settings is not None:
            self._settings = settings
        elif path == ":memory:":
            self._settings = SettingsStore(path=Path(":memory:"))
        else:
            self._settings = SettingsStore(path=Path(path).parent / "felix-settings.json")
        self._on_trading_change = None
        self._lifecycle = None
        # Wired post-construction by main.py -- closes over
        # _trading_forward_record/_trading_broker, neither of which exist
        # yet at this constructor's own call time.
        self._reset_paper_fn = None
        self._get_paper_archive_fills_fn = None

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                start_iso   TEXT    NOT NULL,
                end_iso     TEXT,
                recurrence  TEXT,
                last_run_iso TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migration: the real production openmind.db's `events` table
        # predates last_run_iso -- CREATE TABLE IF NOT EXISTS above is a
        # no-op against an existing table, so that column was never added.
        # Every list_due_events() call was throwing "no such column:
        # last_run_iso", silently swallowed by _scheduler_loop's broad
        # except Exception in cerebral/main.py -- the entire autonomous
        # discovery + paper-trade dispatch loop has been dead since
        # whenever this table was first created, never once firing via
        # its recurring event. Same idempotent try/ALTER-except pattern
        # as plugins/job_search.py's own column migrations.
        try:
            self._con.execute("ALTER TABLE events ADD COLUMN last_run_iso TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        self._con.commit()

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="create_event",
                description="Creates a calendar event. Recurrence: 'daily', 'weekly', 'monthly', or omit.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title":      {"type": "string"},
                        "start_iso":  {"type": "string", "description": "ISO 8601 datetime, e.g. '2026-05-03T09:00:00'"},
                        "end_iso":    {"type": "string", "description": "Optional ISO 8601 end datetime"},
                        "recurrence": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                    },
                    "required": ["title", "start_iso"],
                },
            ),
            Tool(
                name="list_events",
                description="Returns events, optionally filtered by date range.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "from_iso": {"type": "string", "description": "Include events starting on or after this ISO datetime"},
                        "to_iso":   {"type": "string", "description": "Include events starting on or before this ISO datetime"},
                    },
                },
            ),
            Tool(
                name="update_event",
                description="Updates one or more fields of an existing event.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "id":         {"type": "integer"},
                        "title":      {"type": "string"},
                        "start_iso":  {"type": "string"},
                        "end_iso":    {"type": "string"},
                        "recurrence": {"type": "string"},
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="delete_event",
                description="Deletes an event by id.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            ),
            Tool(
                name="run_gauntlet",
                description=(
                    "Validates a trading strategy against the full validation gauntlet "
                    "(out-of-sample, walk-forward, Monte Carlo, vs-random, vs-benchmark, "
                    "noise, parameter sensitivity, costs, capacity). A VALIDATED verdict "
                    "auto-registers the strategy and schedules it for autonomous paper "
                    "trading -- this is the production entry point S9/S10's dispatch "
                    "chain has no other way to reach."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python source: def strategy(data) -> signals"},
                        "claim": {"type": "string", "description": "Trading hypothesis text to generate code from (alternative to code)"},
                        "url": {"type": "string", "description": "URL to extract a trading claim from (alternative to code)"},
                        "book": {"type": "string", "description": "Book title, with chapter, as an alternative to code"},
                        "chapter": {"type": "string", "description": "Chapter number, paired with book"},
                        "symbol": {"type": "string", "description": "Ticker to backtest and, on VALIDATED, paper-trade"},
                        "hypothesis": {"type": "string", "description": "Falsifiable claim the strategy is testing"},
                        "provenance": {"type": "string", "description": "Where the strategy came from (URL, book claim, 'user, verbatim')"},
                    },
                    "required": ["symbol", "hypothesis"],
                },
            ),
            Tool(
                name="edit_strategy",
                description=(
                    "Edits an existing strategy's source code: records a new version, "
                    "re-runs the full validation gauntlet against the edited code, and "
                    "only moves the strategy's live dispatch pointer to the new version "
                    "if it validates. A failed edit leaves the strategy running its "
                    "last-good version."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "strategy_id": {"type": "string", "description": "The strategy to edit (its existing strategy_id)"},
                        "code": {"type": "string", "description": "The new Python source: def strategy(data) -> signals"},
                    },
                    "required": ["strategy_id", "code"],
                },
            ),
            Tool(
                name="get_strategy_code",
                description="Returns a strategy's currently dispatched source code and its rendered provenance.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "strategy_id": {"type": "string"},
                    },
                    "required": ["strategy_id"],
                },
            ),
            Tool(
                name="expand_strategy_ticker",
                description=(
                    "Expands a validated strategy to new candidate tickers by running the full "
                    "validation gauntlet. Requires the strategy's confidence weight to be positive. "
                    "Candidate tickers are drawn from the known liquid universe, ranked by "
                    "day-trading suitability, and capped by the discovery candidate limit. "
                    "Each successful candidate registers as a new strategy row suffixed with @SYMBOL."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "strategy_id": {"type": "string", "description": "The strategy to expand"},
                    },
                    "required": ["strategy_id"],
                },
            ),
            Tool(
                name="mix_strategies",
                description=(
                    "Combines multiple validated strategies into a single composite strategy. "
                    "Resolves each component by strategy_id, validates they share the same symbol, "
                    "generates the composite source code, and runs the full validation gauntlet. "
                    "Modes: 'unanimous' (requires exact agreement, else 0), 'majority' (sign of sum, ties 0)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "component_ids": {"type": "array", "items": {"type": "string"}, "description": "List of strategy_ids to mix"},
                        "mode": {"type": "string", "enum": ["unanimous", "majority"], "description": "Voting mode"}
                    },
                    "required": ["component_ids", "mode"],
                },
            ),
            Tool(
                name="auto_combine_strategies",
                description=(
                    "S43: Automatically selects the top-3 validated strategies for a given symbol by "
                    "confidence weight, combines them using both 'unanimous' and 'majority' voting, "
                    "and runs the validation gauntlet on both. Returns the better-performing composite. "
                    "Requires at least 2 eligible strategies with positive confidence weight."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Ticker to auto-compose strategies for"},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="start_trading",
                description=(
                    "S34/#901: enable the autonomous paper-trading dispatch loop "
                    "(default already on -- this is for re-enabling after stop_trading). "
                    "Does not affect trading_live_arm or any already-graduated live strategy."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="stop_trading",
                description="S34/#901: pause the autonomous paper-trading dispatch loop.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="reset_paper_trading",
                description=(
                    "Clears the live paper-trading view (fills, P&L, equity "
                    "curves on the Overview/Trade Log tabs) and resets the "
                    "simulated paper account back to its configured starting "
                    "capital. Does not touch any 'live' fills or trading_live_arm. "
                    "Not destructive -- the cleared history is archived as its "
                    "own block, viewable in the Overview tab's collapsible "
                    "history section."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_paper_archive_fills",
                description=(
                    "Read-only: the real fills for one archived paper-trading "
                    "block created by reset_paper_trading, for the Overview "
                    "tab's collapsible history section (expand-to-fetch)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"archive_id": {"type": "integer"}},
                    "required": ["archive_id"],
                },
            ),
            Tool(
                name="halt_strategy",
                description=(
                    "2026-08-27: manually halts a strategy's autonomous dispatch "
                    "(paper or live) -- reversible via resume_strategy. Keeps all "
                    "history (fills, lineage); only stops future scheduled ticks."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"strategy_id": {"type": "string"}},
                    "required": ["strategy_id"],
                },
            ),
            Tool(
                name="resume_strategy",
                description=(
                    "2026-08-27: reverses a halt (manual or automatic) -- resumes "
                    "at 'paper' status; re-earns live status through the normal "
                    "30-trade graduation gate again rather than resuming live "
                    "immediately."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"strategy_id": {"type": "string"}},
                    "required": ["strategy_id"],
                },
            ),
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
        if tool_name == "create_event":
            return self._create_event(args)
        if tool_name == "list_events":
            return self._list_events(args)
        if tool_name == "update_event":
            return self._update_event(args)
        if tool_name == "delete_event":
            return self._delete_event(args)
        if tool_name == "run_gauntlet":
            return await self._run_gauntlet(args)
        if tool_name == "edit_strategy":
            return await self._edit_strategy(args)
        if tool_name == "start_trading":
            return self._start_trading(args)
        if tool_name == "stop_trading":
            return self._stop_trading(args)
        if tool_name == "reset_paper_trading":
            return await self._reset_paper_trading(args)
        if tool_name == "get_paper_archive_fills":
            return self._get_paper_archive_fills(args)
        if tool_name == "get_strategy_code":
            return self._get_strategy_code(args)
        if tool_name == "expand_strategy_ticker":
            return await self._expand_strategy_ticker(args)
        if tool_name == "mix_strategies":
            return await self._run_mix_strategies(args)
        if tool_name == "auto_combine_strategies":
            return await self._run_auto_combine_strategies(args)
        if tool_name == "halt_strategy":
            return self._halt_strategy(args)
        if tool_name == "resume_strategy":
            return self._resume_strategy(args)
        if tool_name == "check_ipo_calendar":
            return await self._check_ipo_calendar(args)
        if tool_name == "dispatch_due_ipos":
            return await self._dispatch_due_ipos(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    def _create_event(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        start_iso = args.get("start_iso", "").strip()
        end_iso = args.get("end_iso")
        recurrence = args.get("recurrence")

        if not title:
            return ToolResult(content="title is required", is_error=True)
        if not start_iso:
            return ToolResult(content="start_iso is required", is_error=True)
        if recurrence and not _is_valid_recurrence(recurrence):
            return ToolResult(
                content=(
                    f"recurrence must be one of {sorted(_VALID_RECURRENCES)} "
                    "or a short interval like '5m'/'1h'"
                ),
                is_error=True,
            )

        cur = self._con.execute(
            "INSERT INTO events (title, start_iso, end_iso, recurrence) VALUES (?, ?, ?, ?)",
            (title, start_iso, end_iso, recurrence),
        )
        self._con.commit()
        return ToolResult(content=json.dumps({"id": cur.lastrowid, "title": title}))

    def _list_events(self, args: dict) -> ToolResult:
        from_iso = args.get("from_iso")
        to_iso = args.get("to_iso")

        query = "SELECT * FROM events"
        params: list = []
        clauses: list[str] = []
        if from_iso:
            clauses.append("start_iso >= ?")
            params.append(from_iso)
        if to_iso:
            clauses.append("start_iso <= ?")
            params.append(to_iso)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY start_iso"

        rows = self._con.execute(query, params).fetchall()
        events = [_row_to_event(r) for r in rows]
        return ToolResult(content=json.dumps({"events": events}))

    def list_due_events(self) -> list[dict]:
        """Return events due for dispatch right now (S7-S9 autonomous
        paper-trade loop): a never-run event whose start_iso has passed, or
        a recurring event whose recurrence interval has elapsed since
        last_run_iso.

        Filters candidates in SQL (recurring, or never run) then checks the
        actual due-ness in Python -- comparing "now minus a per-recurrence
        timedelta" against last_run_iso doesn't reduce to a single SQL
        expression cleanly across daily/weekly/monthly/Nm/Nh, and the
        candidate set (one event per active strategy) is small enough that
        this never needs to be a database-side filter.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = self._con.execute(
            "SELECT * FROM events WHERE recurrence IS NOT NULL OR last_run_iso IS NULL"
        ).fetchall()
        due = []
        for row in rows:
            start = _parse_iso(row["start_iso"])
            last_run = _parse_iso(row["last_run_iso"])
            if last_run is None:
                if start is not None and start <= now:
                    due.append(row)
                continue
            interval = _recurrence_interval(row["recurrence"])
            if interval is not None and now - last_run >= interval:
                due.append(row)
        return [_row_to_event(r) for r in due]

    def mark_event_run(self, event_id: int) -> None:
        """Records that a due event was just dispatched (S7-S9). Kept
        separate from _run_paper_strategy -- that method's job is placing a
        trade, not managing event bookkeeping; the dispatcher (which already
        holds the event row) calls this after a successful dispatch."""
        run_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        self._con.execute("UPDATE events SET last_run_iso=? WHERE id=?", (run_iso, event_id))
        self._con.commit()

    def _update_event(self, args: dict) -> ToolResult:
        event_id = args.get("id")
        if event_id is None:
            return ToolResult(content="id is required", is_error=True)

        # Verify it exists
        row = self._con.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            return ToolResult(content=f"Event {event_id} not found", is_error=True)

        updatable = {k: v for k, v in args.items() if k != "id" and k in {"title", "start_iso", "end_iso", "recurrence"}}
        if not updatable:
            return ToolResult(content=json.dumps({"id": event_id, "updated": []}))

        set_clause = ", ".join(f"{k}=?" for k in updatable)
        values = list(updatable.values()) + [event_id]
        self._con.execute(f"UPDATE events SET {set_clause} WHERE id=?", values)
        self._con.commit()
        return ToolResult(content=json.dumps({"id": event_id, "updated": list(updatable.keys())}))

    def _delete_event(self, args: dict) -> ToolResult:
        event_id = args.get("id")
        if event_id is None:
            return ToolResult(content="id is required", is_error=True)

        row = self._con.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            return ToolResult(content=f"Event {event_id} not found", is_error=True)

        self._con.execute("DELETE FROM events WHERE id=?", (event_id,))
        self._con.commit()
        return ToolResult(content=json.dumps({"id": event_id, "deleted": True}))

    async def _run_gauntlet(
        self, args: dict, *, strategy_store=None, fetch=None,
        origin: str = "generated", parent_version=None, strategy_id: "str | None" = None,
        components_json=None, interval: str = "1d",
    ) -> ToolResult:
        """S11 Part 3: the production entry point for run_gauntlet.

        Builds a real backtest wrapper around the compiled strategy (not a
        mock, not a hardcoded equity curve -- the two ways the first attempt
        at this, closed unmerged as PR #855, was broken) and calls the real
        `cerebral.trading.gauntlet.run_gauntlet`. A VALIDATED verdict flows
        straight into gauntlet.py's own existing auto-promote block (`self`
        as `scheduler`, a fresh StubBrokerClient as `paper_broker`), which
        registers a StrategySpec and schedules a recurring event -- the same
        chain S9/S10 already built and tested; this call site's only job is
        making sure that chain is ever reached in production at all.

        S15b (#860): `code` can also be generated from a `claim`/`url`/
        `book`+`chapter` via `to_strategy` (S15's real, router-backed
        generator) instead of being supplied directly -- async because
        `to_strategy` itself is (it awaits the model router).

        `strategy_store`/`fetch` are test-only injection seams (not part of
        the Tool schema an LLM sees) -- default to the real StrategyStore /
        yfinance-backed fetch_ohlcv, matching `_run_paper_strategy`'s own
        `store=None, fetch=None` convention.
        """
        code = args.get("code", "").strip()
        claim = args.get("claim", "").strip()
        url = args.get("url", "").strip()
        book = args.get("book", "").strip()
        chapter = args.get("chapter", "").strip()
        symbol = args.get("symbol", "").strip()
        hypothesis = args.get("hypothesis", "").strip()
        provenance = args.get("provenance", "")
        interval = args.get("interval", "1d")

        if not symbol or not hypothesis:
            return ToolResult(content="symbol and hypothesis are required", is_error=True)

        idea = None
        if not code:
            from cerebral.trading_ideas import from_prose, from_book_claim, extract_from_url, to_strategy

            # book+chapter checked before bare claim (2026-08-26) so a
            # caller with a specific claim AND book provenance (book
            # ingestion) gets from_book_claim(claim, book, chapter) --
            # correct provenance, real claim text -- instead of losing the
            # book/chapter tagging to from_prose's generic "user, verbatim"
            # provenance. Every pre-existing caller passes exactly one of
            # claim/book+chapter, never both, so this is additive: claim-
            # only and book+chapter-only behavior are both unchanged.
            if book and chapter:
                idea = from_book_claim(claim or f"Hypothesis from {book}", book, chapter)
            elif claim:
                idea = from_prose(claim)
            elif url:
                ideas = extract_from_url(url)
                if not ideas:
                    return ToolResult(content=f"No claims extracted from {url}", is_error=True)
                idea = ideas[0]
            else:
                return ToolResult(
                    content="One of code, claim, book+chapter, or url is required",
                    is_error=True,
                )

            code = await to_strategy(idea, router=self._router)
            if not code:
                return ToolResult(content="Strategy generation produced no code", is_error=True)

        if fetch is None:
            from cerebral.trading_data import fetch_ohlcv as fetch
        from cerebral.trading.sandboxed_eval import evaluate_signals, evaluate_signals_verbose

        end = datetime.now(timezone.utc).date()
        # Intraday bars don't need 365 calendar days; use interval-derived lookback
        lookback_days = 365 if interval == "1d" else 30
        start = end - timedelta(days=lookback_days)
        try:
            prices = fetch(symbol, start.isoformat(), end.isoformat(), interval=interval)
        except Exception as e:
            return ToolResult(content=f"Data fetch failed for {symbol}: {e}", is_error=True)

        if idea is not None:
            _, _repair_err = evaluate_signals_verbose(code, prices)
            if _repair_err:
                _repaired = await to_strategy(
                    idea, router=self._router, prior_code=code, prior_error=_repair_err,
                )
                if _repaired:
                    code = _repaired
                    provenance = provenance + " (repaired after 1 retry)"

        def backtest(bars, params):
            equity, _position, metrics = run_bars(code, bars, interval)
            return equity, metrics

        # Fractional-share sizing at registration (found live 2026-09-01):
        # position_qty used to be a hardcoded 1.0 regardless of price or
        # account size -- 1 share of any $100+ stock instantly blew past
        # RiskManager's per-trade-risk cap on the real (small) paper
        # account, silently blocking almost every real signal forever.
        # Alpaca and StubBrokerClient both already accept fractional qty;
        # nothing previously computed one. Sized to the FULL risk budget
        # (user call, 2026-09-01: ~$10/stock across a $100/10-position
        # account, no headroom margin) -- a struggling strategy re-sizes on
        # its next registration, so drift between registration and first
        # dispatch tick isn't worth trading off against hitting the target
        # size exactly. Deliberately NOT touched: the ramp (25%/50%/100%)
        # and confidence-weight multiplier in live_tick.py's
        # run_strategy_tick, which multiply this registered qty at dispatch
        # time -- those are separate, already-tested mechanisms this only
        # feeds a sane starting value into.
        last_price = float(prices["Close"].iloc[-1]) if "Close" in prices.columns and len(prices) else 0.0
        risk_pct = self._settings.get("max_per_trade_risk_pct") or 2.0
        starting_capital = self._settings.get("trading_paper_starting_capital") or 10000.0
        # FIXME: SchedulerPlugin doesn't expose self._trading_broker yet. If/when it does,
        # prefer it here to avoid drift with the app setting:
        #   if self._trading_broker is not None:
        #       try: starting_capital = self._trading_broker.get_account().equity
        #       except Exception: pass
        position_qty = (starting_capital * (risk_pct / 100.0)) / last_price if last_price > 0 else 1.0

        try:
            # ponytail: benchmark is the strategy's own buy-and-hold, not a
            # real index (SPY) -- run_gauntlet's vs-benchmark gate needs
            # *some* series; wiring a shared SPY fetch is a separate slice.
            card = run_gauntlet(
                backtest, prices, {}, prices.copy(),
                position_sizes=pd.Series([position_qty] * len(prices), index=prices.index),
                hypothesis=hypothesis, provenance=provenance,
                scheduler=self, paper_broker=StubBrokerClient(),
                symbol=symbol, strategy_code=code,
                strategy_store=strategy_store, position_qty=position_qty,
                origin=origin, parent_version=parent_version, strategy_id=strategy_id,
                components_json=components_json, interval=interval,
            )
        except Exception as e:
            logger.warning(f"[scheduler] run_gauntlet failed for {symbol}: {e}", exc_info=True)
            return ToolResult(content=f"Gauntlet run failed: {e}", is_error=True)

        return ToolResult(content=json.dumps({
            "verdict": card.verdict,
            "sharpe": card.sharpe,
            "total_return": card.total_return,
            # StrategyCard has no strategy_name field -- mirror the exact same
            # derivation cerebral.trading.gauntlet.run_gauntlet uses internally
            # (strategy_name = strategy_id or hypothesis or provenance) so the
            # reported value matches whatever store.save() actually used.
            "strategy_id": strategy_id or hypothesis or provenance,
            "gates": [
                {"name": g.name, "passed": bool(g.passed), "details": g.details}
                for g in card.gates
            ],
        }))

    def ensure_ipo_calendar_event(self, recurrence: str = "7d") -> None:
        """Idempotent get-or-create for the weekly IPO-calendar-refresh event, mirroring
        ensure_discovery_event's own pattern exactly. Safe to call on every boot."""
        existing = self._con.execute(
            "SELECT id FROM events WHERE title = ?", (self.IPO_CALENDAR_EVENT_TITLE,)
        ).fetchone()
        if existing is not None:
            return
        self._create_event({
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
        from cerebral.trading.ipo_strategy import IPO_POP_FADE_STRATEGY_CODE
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
                code=IPO_POP_FADE_STRATEGY_CODE, qty=1.0, interval="5m",
                risk_override_pct=25.0,
            )
            store.save(spec, origin="discovered", hypothesis=f"IPO pop-then-fade play on {entry['ticker']}",
                       provenance_json={"source": f"ipo_calendar: {entry['ticker']} IPO {entry['ipo_date']}"})
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

    def _start_trading(self, args: dict) -> ToolResult:
        self._settings.set("trading_paper_enabled", True)
        return ToolResult(content=json.dumps({"enabled": True}))

    def _stop_trading(self, args: dict) -> ToolResult:
        self._settings.set("trading_paper_enabled", False)
        return ToolResult(content=json.dumps({"enabled": False}))

    async def _reset_paper_trading(self, args: dict) -> ToolResult:
        if self._reset_paper_fn is None:
            return ToolResult(content="Reset not wired up yet.", is_error=True)
        result = self._reset_paper_fn()
        if hasattr(result, "__await__"):
            result = await result
        return ToolResult(content=json.dumps(result or {"reset": True}))

    def _get_paper_archive_fills(self, args: dict) -> ToolResult:
        if self._get_paper_archive_fills_fn is None:
            return ToolResult(content="Not wired up yet.", is_error=True)
        archive_id = args.get("archive_id")
        if archive_id is None:
            return ToolResult(content="archive_id is required.", is_error=True)
        fills = self._get_paper_archive_fills_fn(int(archive_id))
        return ToolResult(content=json.dumps({"fills": fills}))



    def _halt_strategy(self, args: dict) -> ToolResult:
        strategy_id = (args.get("strategy_id") or "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)
        if self._lifecycle is None:
            return ToolResult(content="Strategy lifecycle is not wired", is_error=True)
        self._lifecycle.halt_strategy(strategy_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"strategy_id": strategy_id, "status": "halted"}))

    def _resume_strategy(self, args: dict) -> ToolResult:
        strategy_id = (args.get("strategy_id") or "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)
        if self._lifecycle is None:
            return ToolResult(content="Strategy lifecycle is not wired", is_error=True)
        state = self._lifecycle.get_state(strategy_id)
        if state.status != "halted":
            return ToolResult(content=f"Strategy '{strategy_id}' is not halted (status={state.status})", is_error=True)
        self._lifecycle.resume_strategy(strategy_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"strategy_id": strategy_id, "status": "paper"}))

    async def _edit_strategy(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        """S17 (#862): edit an existing strategy's code -- new version, full
        gauntlet re-run, dispatch pointer only moves on VALIDATED. Delegates
        to _run_gauntlet's existing auto-promote path via the origin/
        parent_version/strategy_id params added for exactly this call --
        no separate gauntlet-calling logic duplicated here."""
        strategy_id = args.get("strategy_id", "").strip()
        code = args.get("code", "").strip()
        if not strategy_id or not code:
            return ToolResult(content="strategy_id and code are required", is_error=True)

        store = strategy_store if strategy_store is not None else StrategyStore()
        spec = store.get(strategy_id)
        parent = store.get_current_version(strategy_id)
        if spec is None or parent is None:
            return ToolResult(content=f"No existing strategy '{strategy_id}' to edit", is_error=True)

        provenance = store.render_provenance(parent) + ", as modified by user"
        return await self._run_gauntlet(
            {
                "code": code, "symbol": spec.symbol,
                "hypothesis": parent["hypothesis"] or "",
                "provenance": provenance,
            },
            strategy_store=store, fetch=fetch,
            origin="user_edited", parent_version=parent["version"], strategy_id=strategy_id,
        )

    def _get_strategy_code(self, args: dict, *, strategy_store=None) -> ToolResult:
        """S17 (#862): companion read -- current dispatched source + provenance."""
        strategy_id = args.get("strategy_id", "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)

        store = strategy_store if strategy_store is not None else StrategyStore()
        spec = store.get(strategy_id)
        if spec is None:
            return ToolResult(content=f"No strategy '{strategy_id}' found", is_error=True)

        version_row = store.get_current_version(strategy_id)
        provenance = store.render_provenance(version_row) if version_row is not None else "unknown"
        return ToolResult(content=json.dumps({"code": spec.code, "provenance": provenance}))

    async def _expand_strategy_ticker(
        self, args: dict, *, strategy_store=None, fetch=None, confidence_fn=None, broker=None,
    ) -> ToolResult:
        """S42: expand a validated strategy to new candidate tickers via the gauntlet.

        `strategy_store`/`fetch` are test-only injection seams, matching
        `_run_gauntlet`'s own convention. `confidence_fn` likewise (defaults to
        S38's real ForwardRecord.compute_confidence_weight) -- a strategy_id
        string in, a float weight out.
        """
        strategy_id = args.get("strategy_id", "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)

        store = strategy_store if strategy_store is not None else StrategyStore()
        spec = store.get(strategy_id)
        if spec is None:
            return ToolResult(content=f"No strategy '{strategy_id}' found", is_error=True)

        if confidence_fn is not None:
            confidence = confidence_fn(strategy_id)
        else:
            from cerebral.trading.forward_record import ForwardRecord
            confidence = ForwardRecord().compute_confidence_weight(strategy_id=strategy_id)

        if confidence <= 0:
            return ToolResult(
                content=f"Strategy '{strategy_id}' has non-positive confidence weight ({confidence}). Cannot expand.",
                is_error=True,
            )

        version_row = store.get_current_version(strategy_id)
        hypothesis = (version_row["hypothesis"] if version_row is not None else "") or f"Hypothesis from {strategy_id}"

        current_symbol = spec.symbol
        candidate_limit = self._settings.get("discovery_candidate_limit") or 3

        fetch_fn = fetch
        if fetch_fn is None:
            from cerebral.trading_data import fetch_ohlcv as fetch_fn

        # DD4 (#1160): the shared Candidate pool (ADR-0026 decision 5, amended
        # 2026-09-08) -- same lazy-broker convention as _run_discovery (DD3).
        broker_obj = broker
        if broker_obj is None:
            from cerebral.trading.broker import AlpacaBrokerClient
            broker_obj = AlpacaBrokerClient(env="paper")
        universe = build_dynamic_universe(broker_obj, fetch_fn)
        candidates = [t for t in universe if t != current_symbol]
        ranked_candidates = rank_for_day_trading(candidates, fetch_fn)
        candidates = ranked_candidates[:candidate_limit]

        results = []
        for candidate in candidates:
            new_id = mint_expansion_strategy_id(strategy_id, candidate)
            gauntlet_args = {
                "code": spec.code,
                "symbol": candidate,
                "hypothesis": hypothesis,
                "provenance": f"Expanded from {strategy_id} for {candidate}",
            }
            try:
                result = await self._run_gauntlet(
                    gauntlet_args,
                    strategy_store=store,
                    fetch=fetch,
                    origin="discovered",
                    strategy_id=new_id,
                )
                verdict = json.loads(result.content).get("verdict", "ERROR") if not result.is_error else "ERROR"
            except Exception as exc:
                verdict = "ERROR"
                logger.exception("[scheduler] expand_strategy_ticker gauntlet dispatch failed for %s", candidate)
            results.append({
                "ticker": candidate,
                "new_id": new_id,
                "verdict": verdict,
            })

        if self._record_activity_fn is not None:
            await self._record_activity_fn(
                "activity",
                {
                    "source": "expand_strategy_ticker",
                    "strategy_id": strategy_id,
                    "tickers": [r["ticker"] for r in results],
                    "verdicts": {r["ticker"]: r["verdict"] for r in results},
                }
            )

        return ToolResult(content=json.dumps({
            "original_id": strategy_id,
            "attempts": results,
        }))

    async def _run_mix_strategies(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        component_ids = args.get("component_ids", [])
        mode = args.get("mode", "")
        if not component_ids or mode not in ("unanimous", "majority"):
            return ToolResult(content="component_ids (list) and mode (unanimous/majority) are required", is_error=True)

        if strategy_store is not None:
            store = strategy_store
        else:
            from cerebral.trading.strategy_store import StrategyStore
            store = StrategyStore()

        resolved = []
        symbols = set()
        provenances = []
        for cid in component_ids:
            spec = store.get(cid)
            version = store.get_current_version(cid)
            if spec is None or version is None:
                return ToolResult(content=f"Component strategy '{cid}' not found in store", is_error=True)
            symbols.add(spec.symbol)
            provenances.append(store.render_provenance(version))
            resolved.append((cid, spec.code))

        if len(symbols) > 1:
            return ToolResult(
                content=f"Mismatched symbols across components: {sorted(symbols)}. All components must share the same symbol.",
                is_error=True,
            )
        
        symbol = symbols.pop()
        
        try:
            from cerebral.trading.compose import compose_strategies
            composite_code = compose_strategies(resolved, mode)
        except Exception as e:
            return ToolResult(content=f"Composite generation failed: {e}", is_error=True)

        import uuid
        new_id = f"mixed_{uuid.uuid4().hex[:8]}"

        # A real Python object, not a pre-serialized string: store.save()
        # does its own json.dumps on whatever components_json is given (see
        # cerebral/trading/strategy_store.py), and render_provenance's
        # 'mixed' branch reads this column back to name every component --
        # the earlier version of this method packed the same information
        # into the `provenance` string instead, which never reaches
        # strategy_versions.components_json at all (that column stayed
        # NULL forever, so render_provenance could never actually name a
        # component -- a real bug, not just an unused parameter).
        components = [{"id": cid, "provenance": p} for cid, p in zip(component_ids, provenances)]
        provenance_str = f"Mixed strategy ({mode}) of {len(component_ids)} components: {', '.join(component_ids)}"

        return await self._run_gauntlet(
            {
                "code": composite_code,
                "symbol": symbol,
                "hypothesis": f"Composite strategy ({mode}) of {len(component_ids)} components",
                "provenance": provenance_str,
            },
            strategy_store=store, fetch=fetch,
            origin="mixed", strategy_id=new_id, components_json=components,
        )

    async def _run_auto_combine_strategies(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        """S43: Same-symbol composite auto-discovery tool. Selects top-3 by confidence,
        runs both unanimous and majority modes, and keeps the better-performing result."""
        symbol = args.get("symbol", "").strip()
        if not symbol:
            return ToolResult(content="symbol is required", is_error=True)

        store = strategy_store if strategy_store is not None else StrategyStore()
        symbol_strategies = [s for s in store.list_all() if s.symbol == symbol]
        
        from cerebral.trading.forward_record import ForwardRecord
        record = ForwardRecord()
        scored = []
        for s in symbol_strategies:
            try:
                conf = record.compute_confidence_weight(strategy_id=s.strategy_id)
                if conf > 0:
                    scored.append((conf, s.strategy_id))
            except Exception:
                continue
                
        scored.sort(key=lambda x: x[0], reverse=True)
        top_3 = scored[:3]
        
        if len(top_3) < 2:
            return ToolResult(content=json.dumps({
                "status": "not_enough_strategies",
                "eligible_count": len(top_3),
                "message": f"Need at least 2 eligible strategies (confidence > 0), found {len(top_3)} on {symbol}."
            }))
            
        component_ids = [sid for _, sid in top_3]
        
        res_uni = await self._run_mix_strategies(
            {"component_ids": component_ids, "mode": "unanimous"},
            strategy_store=store, fetch=fetch
        )
        res_maj = await self._run_mix_strategies(
            {"component_ids": component_ids, "mode": "majority"},
            strategy_store=store, fetch=fetch
        )
        
        def parse(res):
            try:
                d = json.loads(res.content)
                return d.get("total_return", 0.0), d.get("verdict", "ERROR"), d.get("strategy_id")
            except Exception:
                return 0.0, "ERROR", None

        ret_uni, ver_uni, id_uni = parse(res_uni)
        ret_maj, ver_maj, id_maj = parse(res_maj)

        if ver_uni == "VALIDATED" and ver_maj != "VALIDATED":
            winner, loser = "unanimous", "majority"
        elif ver_maj == "VALIDATED" and ver_uni != "VALIDATED":
            winner, loser = "majority", "unanimous"
        else:
            winner = "unanimous" if ret_uni >= ret_maj else "majority"
            loser = "majority" if winner == "unanimous" else "unanimous"

        winner_ret, winner_ver = (ret_uni, ver_uni) if winner == "unanimous" else (ret_maj, ver_maj)
        loser_id = id_maj if winner == "unanimous" else id_uni

        # Only the winner may end up persisted as a validated strategy --
        # run_gauntlet only saves a StrategySpec when its own verdict is
        # VALIDATED (cerebral/trading/gauntlet.py), so this is a real delete
        # when the loser also validated, and a harmless no-op (nothing to
        # delete) when it didn't. Both branches were previously reached via
        # a store.get/getattr('origin'/'provenance') lookup that doesn't
        # exist on StrategySpec, silently swallowed by a bare except -- this
        # uses the loser's own real strategy_id instead, surfaced by
        # _run_mix_strategies above.
        if loser_id is not None:
            store.delete(loser_id)

        if self._record_activity_fn is not None:
            await self._record_activity_fn(
                "activity",
                {
                    "source": "auto_combine_strategies",
                    "symbol": symbol,
                    "component_ids": component_ids,
                    "winner_mode": winner,
                    "winner_strategy_id": id_uni if winner == "unanimous" else id_maj,
                    "winner_verdict": winner_ver,
                }
            )

        return ToolResult(content=json.dumps({
            "status": "complete",
            "symbol": symbol,
            "component_ids": component_ids,
            "winner_mode": winner,
            "winner_strategy_id": id_uni if winner == "unanimous" else id_maj,
            "winner_return": winner_ret,
            "winner_verdict": winner_ver,
            "loser_mode": loser,
        }))

    def _run_paper_strategy(
        self, strategy_name: str, broker, forward_record: "ForwardRecord",
        config: dict | None = None, store=None, fetch=None, phase: str = "paper",
        dispatch_id: str | None = None,
        risk=None, size_pct: float = 1.0,  # S20
        sentiment_label: str | None = None,
        stock_sentiment_labels: dict | None = None,
        claimed_symbols: set | None = None,
        bear_case_fn=None,
        correlation_matrix: pd.DataFrame | None = None,
    ) -> dict:
        """Runs one paper-trading tick for the given strategy. Pure trade
        execution -- event bookkeeping (marking a due event as dispatched)
        is the caller's job; see mark_event_run().

        The real decision (fetch data -> evaluate the strategy -> diff against
        the broker's own position -> open/close/hold -> record realized P&L)
        lives in cerebral/trading/live_tick.py; this is the plugin-side seam.
        It used to place a hardcoded `buy 1 "SYMBOL"` on every call -- a
        literal placeholder ticker, always buy, never sell, no strategy
        consulted at all.

        `config` may carry an inline spec ({"symbol", "code", "qty"}); with
        no code, the strategy's registered spec is looked up by name. No
        spec means no trade -- there is no default symbol to fall back to.
        """
        if not broker or not forward_record:
            return {"status": "skipped", "reason": "broker/record not provided"}
        config = config or {}

        try:
            if config.get("code"):
                spec = StrategySpec(
                    strategy_id=strategy_name, symbol=config["symbol"],
                    code=config["code"], qty=config.get("qty", 1.0),
                )
            else:
                spec = (store or StrategyStore()).get(strategy_name)
            if spec is None:
                return {"status": "skipped", "reason": "no strategy spec registered"}

            result = run_strategy_tick(
                dispatch_id or strategy_name, spec, broker, forward_record, fetch=fetch, phase=phase,
                risk=risk, size_pct=size_pct,  # S20
                position_key=strategy_name,  # #961: broker position survives a version bump
                sentiment_label=sentiment_label,
                stock_sentiment_labels=stock_sentiment_labels,
                claimed_symbols=claimed_symbols,
                bear_case_fn=bear_case_fn,
                correlation_matrix=correlation_matrix,
            )
            logger.info(f"Paper tick for {strategy_name}: {result}")
            return result
        except Exception as e:
            logger.warning(f"Paper trade execution failed for {strategy_name}: {e}")
            return {"status": "error", "reason": str(e)}


def _row_to_event(row: sqlite3.Row) -> dict:
    return {
        "id":         row["id"],
        "title":      row["title"],
        "start_iso":  row["start_iso"],
        "end_iso":    row["end_iso"],
        "recurrence": row["recurrence"],
        "created_at": row["created_at"],
    }


def create() -> SchedulerPlugin:
    return SchedulerPlugin()
