"""
Scheduler plugin — MCP server for Felix.

Tools: create_event, list_events, update_event, delete_event, run_gauntlet.
SQLite-backed (same openmind.db). No external calendar deps.
"""
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading.live_tick import run_strategy_tick
from cerebral.trading.strategy_store import StrategySpec, StrategyStore
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.gauntlet import run_gauntlet

logger = logging.getLogger(__name__)

PLUGIN_NAME = "scheduler"

# ADR-0005 / Issue #44 — list_events reads SQLite (fs_read); create_event /
# update_event / delete_event mutate the events table (fs_write). The DB
# file itself is never unlinked, so fs_delete is not needed.
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
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class SchedulerPlugin:
    name = PLUGIN_NAME

    def __init__(self, db_path=None):
        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

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
                        "symbol": {"type": "string", "description": "Ticker to backtest and, on VALIDATED, paper-trade"},
                        "hypothesis": {"type": "string", "description": "Falsifiable claim the strategy is testing"},
                        "provenance": {"type": "string", "description": "Where the strategy came from (URL, book claim, 'user, verbatim')"},
                    },
                    "required": ["code", "symbol", "hypothesis"],
                },
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
            return self._run_gauntlet(args)
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

    def _run_gauntlet(
        self, args: dict, *, strategy_store=None, fetch=None,
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

        `strategy_store`/`fetch` are test-only injection seams (not part of
        the Tool schema an LLM sees) -- default to the real StrategyStore /
        yfinance-backed fetch_ohlcv, matching `_run_paper_strategy`'s own
        `store=None, fetch=None` convention.
        """
        code = args.get("code", "").strip()
        symbol = args.get("symbol", "").strip()
        hypothesis = args.get("hypothesis", "").strip()
        provenance = args.get("provenance", "")
        if not code or not symbol or not hypothesis:
            return ToolResult(content="code, symbol, and hypothesis are required", is_error=True)

        if fetch is None:
            from cerebral.trading_data import fetch_ohlcv as fetch
        from cerebral.trading.sandboxed_eval import evaluate_signals

        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=365)
        try:
            prices = fetch(symbol, start.isoformat(), end.isoformat())
        except Exception as e:
            return ToolResult(content=f"Data fetch failed for {symbol}: {e}", is_error=True)

        def backtest(bars, params):
            signals = evaluate_signals(code, bars)
            # A strategy may return fewer signals than bars (indicator
            # warm-up) -- right-align: the LAST signal pairs with the LAST
            # bar, same convention as evaluate_signal's own live-tick read.
            if len(signals) < len(bars):
                signals = [0] * (len(bars) - len(signals)) + list(signals)
            signals = pd.Series(signals, index=bars.index)
            # Yesterday's decided position earns today's return -- using the
            # same-bar signal would let the strategy trade on a close it
            # hasn't seen yet.
            position = signals.shift(1).fillna(0.0)
            daily_returns = position * bars["Close"].pct_change().fillna(0.0)
            equity = 100.0 * (1.0 + daily_returns).cumprod()
            return list(equity), {}

        try:
            # ponytail: benchmark is the strategy's own buy-and-hold, not a
            # real index (SPY) -- run_gauntlet's vs-benchmark gate needs
            # *some* series; wiring a shared SPY fetch is a separate slice.
            card = run_gauntlet(
                backtest, prices, {}, prices.copy(),
                position_sizes=pd.Series([1.0] * len(prices), index=prices.index),
                hypothesis=hypothesis, provenance=provenance,
                scheduler=self, paper_broker=StubBrokerClient(),
                symbol=symbol, strategy_code=code,
                strategy_store=strategy_store, position_qty=1.0,
            )
        except Exception as e:
            logger.warning(f"[scheduler] run_gauntlet failed for {symbol}: {e}", exc_info=True)
            return ToolResult(content=f"Gauntlet run failed: {e}", is_error=True)

        return ToolResult(content=json.dumps({
            "verdict": card.verdict,
            "sharpe": card.sharpe,
            "total_return": card.total_return,
            "gates": [{"name": g.name, "passed": bool(g.passed)} for g in card.gates],
        }))

    def _run_paper_strategy(
        self, strategy_name: str, broker, forward_record: "ForwardRecord",
        config: dict | None = None, store=None, fetch=None, phase: str = "paper",
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
                strategy_name, spec, broker, forward_record, fetch=fetch, phase=phase
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
