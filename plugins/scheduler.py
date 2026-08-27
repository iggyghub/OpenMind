"""
Scheduler plugin — MCP server for Felix.

Tools: create_event, list_events, update_event, delete_event, run_gauntlet.
SQLite-backed (same openmind.db). No external calendar deps.
"""
import asyncio
import base64
import json
import logging
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading.live_tick import run_strategy_tick
from cerebral.trading.strategy_store import StrategySpec, StrategyStore
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.gauntlet import run_gauntlet
from cerebral.trading.discovery import (
    DiscoveryAttempts, DiscoveryWatchlist, rank_for_day_trading, run_discovery_pass,
)
from cerebral.trading.books import (
    BookStore, chunk_text, extract_claims_from_chunk, extract_full_text,
    ingest_book, list_validated_strategies,
)
from cerebral.trading_ideas import Idea, judge_idea as _judge_idea
from cerebral.settings import SettingsStore

logger = logging.getLogger(__name__)

PLUGIN_NAME = "scheduler"

# ADR-0005 / Issue #44 — list_events reads SQLite (fs_read); create_event /
# update_event / delete_event mutate the events table (fs_write).
# 2026-08-27: delete_book's shutil.rmtree() on a book's stored upload
# directory needs fs_delete too -- the DB file itself is still never
# unlinked, but a book's underlying source file now can be.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write", "fs_delete"})

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

    # S27 (#880): the one recurring event title that means "run the
    # discovery loop", not a strategy dispatch -- checked and consumed
    # (mark_event_run) by cerebral/main.py's _scheduler_loop BEFORE
    # dispatch_due_events gets its own turn at list_due_events(), so the
    # per-strategy dispatcher never mistakes it for a strategy to run.
    DISCOVERY_EVENT_TITLE = "__autonomous_discovery__"

    def __init__(self, db_path=None, router=None, web_search_fn=None,
                 record_activity_fn=None, discovery_watchlist=None,
                 discovery_attempts=None, settings=None, book_store=None):
        self._router = router
        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()
        # S27 (#880): web_search_fn/record_activity_fn are injectable seams
        # (fetch=/store=/risk=/notify_fn='s established convention this
        # campaign uses throughout) -- default to the real BrowserPlugin /
        # a no-op respectively, resolved lazily so importing this module
        # never pulls in plugins/browser.py at module-load time.
        self._web_search_fn = web_search_fn
        self._record_activity_fn = record_activity_fn
        # Derived from this instance's own db_path directory, not a bare
        # DiscoveryWatchlist() default -- every existing test that already
        # isolates SchedulerPlugin(db_path=tmp_path/...) gets an isolated
        # watchlist for free, instead of silently touching the real
        # production discovery_watchlist.db the way a bare default would.
        if discovery_watchlist is not None:
            self._discovery_watchlist = discovery_watchlist
        elif path == ":memory:":
            self._discovery_watchlist = DiscoveryWatchlist(db_path=Path(":memory:"))
        else:
            self._discovery_watchlist = DiscoveryWatchlist(
                db_path=Path(path).parent / "discovery_watchlist.db"
            )
        # S30 (#894): same isolation convention as discovery_watchlist above.
        if discovery_attempts is not None:
            self._discovery_attempts = discovery_attempts
        elif path == ":memory:":
            self._discovery_attempts = DiscoveryAttempts(db_path=Path(":memory:"))
        else:
            self._discovery_attempts = DiscoveryAttempts(
                db_path=Path(path).parent / "discovery_attempts.db"
            )
        # S31 (#896): same isolation convention -- a tmp_path-scoped
        # SchedulerPlugin(db_path=...) test gets its own felix-settings.json
        # instead of silently touching the real one.
        if settings is not None:
            self._settings = settings
        elif path == ":memory:":
            self._settings = SettingsStore(path=Path(":memory:"))
        else:
            self._settings = SettingsStore(path=Path(path).parent / "felix-settings.json")
        # 2026-08-26: same isolation convention -- a tmp_path-scoped
        # SchedulerPlugin(db_path=...) test gets its own books.db.
        if book_store is not None:
            self._book_store = book_store
        elif path == ":memory:":
            self._book_store = BookStore(db_path=Path(":memory:"))
        else:
            self._book_store = BookStore(db_path=Path(path).parent / "books.db")
        self._books_dir = (
            Path(path).parent / "books" if path != ":memory:" else Path(":memory:")
        )
        # Background ingestion tasks keyed by book id -- held here so they
        # aren't garbage-collected mid-run (asyncio only keeps a weak
        # reference to a task once nothing else holds it) and so a second
        # upload of the same book id (can't happen today, ids are
        # AUTOINCREMENT) wouldn't silently overlap.
        self._book_tasks: dict[int, "asyncio.Task"] = {}
        # Wired post-construction by main.py (same pattern as
        # _record_activity_fn) to schedule a _trading_broadcast() so the
        # panel's book-progress bars update live, not just on the next
        # unrelated broadcast or a manual trading_poll.
        self._on_trading_change = None
        self._lifecycle = None

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
                name="run_discovery",
                description=(
                    "S27/#880: one autonomous discovery-loop pass. Sources ideas via "
                    "web_search, screens pattern-general ones through judge_idea and the "
                    "growing ticker watchlist (ticker-specific ideas skip screening), and "
                    "dispatches accepted candidates to run_gauntlet with origin='discovered'. "
                    "Normally triggered by its own recurring scheduler event, not called "
                    "directly by the model."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Web-search queries to source ideas from (default: a day-trading-focused set).",
                        },
                        "interval": {
                            "type": "string",
                            "description": "Bar interval every dispatched candidate is backtested at, e.g. '15m', '5m', '1d' (default: '15m').",
                        },
                    },
                },
            ),
            Tool(
                name="start_discovery",
                description=(
                    "S31/#896: manually enable the discovery loop (default OFF -- it does "
                    "not run on its own until this is called). Pass duration_hours to "
                    "auto-stop after that many hours; omit it to run until stop_discovery is "
                    "called. Pass queries/interval to override run_discovery's own built-in "
                    "defaults for every subsequent pass (e.g. to focus on day-trading "
                    "strategies specifically) -- omitted or empty leaves the current stored "
                    "value unchanged, it does not reset to the built-in default."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Override the queries used for every future discovery pass.",
                        },
                        "interval": {
                            "type": "string",
                            "description": "Override the bar interval used for every future discovery pass.",
                        },
                        "duration_hours": {
                            "type": "number",
                            "description": "Auto-stop after this many hours. Omit to run indefinitely.",
                        },
                        "candidate_limit": {
                            "type": "integer",
                            "description": (
                                "How many candidate tickers a single accepted pattern-general "
                                "idea is tested against per pass (default 10). Omit to leave "
                                "the current stored value unchanged."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="stop_discovery",
                description="S31/#896: immediately disable the discovery loop and clear any auto-stop timer.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_discovery_status",
                description="S31/#896: current discovery enabled/stop_at/queries/interval state.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="upload_book",
                description=(
                    "2026-08-26: upload a book (PDF or plain text) for Felix to read in "
                    "full and pull testable trading-strategy claims out of. Each claim "
                    "found goes through the exact same judge/screen/gauntlet pipeline as "
                    "a web-sourced idea. Processing runs in the background (a real book "
                    "is many LLM passes, one per chunk) -- this returns immediately with "
                    "the book's id and queued status; poll list_books for progress. Call "
                    "once per file for multiple books."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Original filename, e.g. 'market_wizards.pdf'."},
                        "data_base64": {"type": "string", "description": "Base64-encoded file bytes."},
                        "title": {"type": "string", "description": "Book title (defaults to the filename without extension)."},
                    },
                    "required": ["filename", "data_base64"],
                },
            ),
            Tool(
                name="list_books",
                description="2026-08-26: every uploaded book with its ingestion status/progress/strategies-found count.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="stop_book",
                description=(
                    "2026-08-26: cancel a book's in-progress ingestion, freezing its "
                    "progress where it stands (strategies already dispatched are "
                    "unaffected). Also un-sticks a book left stuck at 'processing' "
                    "by a Cerebral restart mid-run, when there's no live task left "
                    "to cancel."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
                },
            ),
            Tool(
                name="retry_book",
                description=(
                    "2026-08-26: redo a book's ingestion from scratch -- cancels any "
                    "in-progress run, re-extracts and re-chunks the original uploaded "
                    "file (still on disk), and reprocesses every chunk again. Does not "
                    "remove strategies/attempts a previous run already dispatched."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
                },
            ),
            Tool(
                name="delete_book",
                description=(
                    "2026-08-26: remove a book's record and its stored file. Cancels "
                    "any in-progress ingestion first. Does not remove strategies/"
                    "attempts already dispatched from this book -- those are "
                    "independent historical records, same as a web-sourced idea's "
                    "attempts surviving even though the source URL isn't stored."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
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
        if tool_name == "run_discovery":
            return await self._run_discovery(args)
        if tool_name == "start_discovery":
            return self._start_discovery(args)
        if tool_name == "stop_discovery":
            return self._stop_discovery(args)
        if tool_name == "get_discovery_status":
            return self._get_discovery_status(args)
        if tool_name == "get_strategy_code":
            return self._get_strategy_code(args)
        if tool_name == "mix_strategies":
            return await self._run_mix_strategies(args)
        if tool_name == "upload_book":
            return await self._upload_book(args)
        if tool_name == "list_books":
            return self._list_books(args)
        if tool_name == "stop_book":
            return self._stop_book(args)
        if tool_name == "retry_book":
            return await self._retry_book(args)
        if tool_name == "delete_book":
            return self._delete_book(args)
        if tool_name == "halt_strategy":
            return self._halt_strategy(args)
        if tool_name == "resume_strategy":
            return self._resume_strategy(args)
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
        from cerebral.trading.sandboxed_eval import evaluate_signals

        end = datetime.now(timezone.utc).date()
        # Intraday bars don't need 365 calendar days; use interval-derived lookback
        lookback_days = 365 if interval == "1d" else 30
        start = end - timedelta(days=lookback_days)
        try:
            prices = fetch(symbol, start.isoformat(), end.isoformat(), interval=interval)
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
            "gates": [
                {"name": g.name, "passed": bool(g.passed), "details": g.details}
                for g in card.gates
            ],
        }))

    # ------------------------------------------------------------------
    # S27 (#880): autonomous discovery loop
    # ------------------------------------------------------------------

    def ensure_discovery_event(self, recurrence: str = "1h") -> None:
        """Idempotent get-or-create for the one recurring discovery event
        (decision #34's own cadence requirement: reuse the existing
        recurring-event mechanism, not a second background loop). Safe to
        call on every boot -- a no-op once the event already exists."""
        existing = self._con.execute(
            "SELECT id FROM events WHERE title = ?", (self.DISCOVERY_EVENT_TITLE,)
        ).fetchone()
        if existing is not None:
            return
        self._create_event({
            "title": self.DISCOVERY_EVENT_TITLE,
            "start_iso": datetime.now(timezone.utc).isoformat(),
            "recurrence": recurrence,
        })

    async def _source_ideas(self, queries: list[str]) -> list["Idea"]:
        """web_search each query, turn hits into Ideas. Real production
        path constructs plugins/browser.py's BrowserPlugin lazily (never
        at module-load time); tests inject web_search_fn instead (SAFETY:
        never a real network call in a test)."""
        if self._web_search_fn is not None:
            search = self._web_search_fn
        else:
            from plugins.browser import BrowserPlugin
            browser = BrowserPlugin()

            async def search(query: str) -> list[dict]:
                result = await browser.call_tool("web_search", {"query": query, "max_results": 3})
                if result.is_error:
                    logger.warning("[scheduler] discovery web_search failed for %r: %s", query, result.content)
                    return []
                data = json.loads(result.content)
                hits = data.get("results", data) if isinstance(data, dict) else data
                return hits if isinstance(hits, list) else []

        ideas: list[Idea] = []
        for query in queries:
            try:
                hits = await search(query)
            except Exception as exc:
                logger.warning("[scheduler] discovery sourcing failed for %r: %s", query, exc)
                continue
            for hit in hits:
                url = hit.get("url") or hit.get("source_url")
                title = hit.get("title") or hit.get("page_title") or ""
                snippet = hit.get("snippet") or hit.get("text") or title
                if not snippet:
                    continue
                ideas.append(Idea(
                    source_url=url, page_title=title, claim_text=snippet,
                    provenance=f"url: {url}" if url else f"web_search: {query}",
                    author_claim_text=f"Author claims: {snippet}",
                ))
        return ideas

    async def _run_discovery(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        """The discovery loop's one trigger: source -> screen -> dispatch.
        `symbol + hypothesis + code -> run_gauntlet` (decision #33) stays
        the single unchanged convergence point -- this only ever calls
        self._run_gauntlet with origin='discovered', never a parallel path.

        `strategy_store`/`fetch` are test-only injection seams (not part of
        the Tool schema), threaded straight through to _run_gauntlet --
        matching its own `store=None, fetch=None` convention. Without this,
        a test exercising run_discovery would silently fall through to
        _run_gauntlet's real yfinance-backed default fetch.

        Default queries (fixed 2026-08-25): the original pair ("quantitative
        trading hypothesis", "stock market anomaly research") only ever
        surfaced textbook/explainer content -- confirmed live, 0/6 accepted
        across two real passes, judge_idea correctly rejecting every one as
        not a specific, falsifiable claim. Queries naming a concrete,
        named strategy (RSI, moving-average crossover) instead of the
        abstract concept of strategy-having surface real backtest/guide
        articles that DO state a specific rule -- confirmed live, 4/6
        accepted with the same unchanged judge.

        Day-trading focus (2026-08-25): default queries name an intraday
        timeframe explicitly, and every dispatch now carries `interval`
        (default "15m", decision #40's own recommended default -- 1m is
        noisier/more data-constrained, and this discovery path has no
        per-idea signal to justify going that granular). Without an
        explicit interval, _run_gauntlet defaults to "1d" and every
        discovered strategy would be a swing/position strategy regardless
        of what the sourced claim was actually about. `_run_gauntlet`'s
        own fetch already goes through Alpaca Market Data first with a
        yfinance intraday fallback (decision #39) -- no new data plumbing
        needed here, just actually asking for it."""
        queries = args.get("queries") or [
            "day trading strategy 5 minute chart backtest",
            "intraday scalping strategy that actually works",
        ]
        interval = args.get("interval") or "15m"

        async def run_gauntlet_fn(idea: Idea, ticker: str) -> dict:
            gauntlet_args = {
                "symbol": ticker,
                "hypothesis": idea.claim_text or "discovered hypothesis",
                "provenance": idea.provenance,
                "interval": interval,
            }
            if idea.source_url:
                gauntlet_args["url"] = idea.source_url
            result = await self._run_gauntlet(
                gauntlet_args, origin="discovered",
                strategy_store=strategy_store, fetch=fetch,
            )
            return {"ticker": ticker, "is_error": result.is_error, "result": result.content}

        async def judge_idea_fn(idea: Idea) -> "tuple[bool, str]":
            return await _judge_idea(idea, router=self._router)

        def rank_fn(symbols: list) -> list:
            fetch_fn = fetch
            if fetch_fn is None:
                from cerebral.trading_data import fetch_ohlcv as fetch_fn
            return rank_for_day_trading(symbols, fetch_fn)

        record_activity_fn = self._record_activity_fn

        async def record_attempt_fn(entry: dict) -> None:
            self._discovery_attempts.record(
                entry["symbol"], entry["verdict"],
                reason=entry.get("reason", ""), idea_url=entry.get("idea_url", ""),
            )

        try:
            ideas = await self._source_ideas(queries)
        except Exception as exc:
            logger.exception("[scheduler] discovery sourcing failed entirely: %s", exc)
            return ToolResult(content=f"Discovery sourcing failed: {exc}", is_error=True)

        candidate_limit = self._settings.get("discovery_candidate_limit")
        results = await run_discovery_pass(
            ideas, self._discovery_watchlist, run_gauntlet_fn,
            judge_idea_fn=judge_idea_fn, record_activity_fn=record_activity_fn,
            record_attempt_fn=record_attempt_fn, rank_fn=rank_fn,
            candidate_limit=candidate_limit,
        )
        return ToolResult(content=json.dumps({
            "sourced": len(ideas), "dispatched": len(results),
        }))

    # ------------------------------------------------------------------
    # S31 (#896): manual discovery start/stop + duration
    # ------------------------------------------------------------------

    def _start_discovery(self, args: dict) -> ToolResult:
        queries = args.get("queries") or []
        interval = (args.get("interval") or "").strip()
        duration_hours = args.get("duration_hours")
        candidate_limit = args.get("candidate_limit")

        self._settings.set("discovery_enabled", True)
        if duration_hours is not None:
            stop_at = (datetime.now(timezone.utc) + timedelta(hours=float(duration_hours))).isoformat()
            self._settings.set("discovery_stop_at", stop_at)
        else:
            self._settings.set("discovery_stop_at", "")
        if queries:
            self._settings.set("discovery_queries", list(queries))
        if interval:
            self._settings.set("discovery_interval", interval)
        if candidate_limit is not None:
            self._settings.set("discovery_candidate_limit", int(candidate_limit))

        return self._get_discovery_status({})

    def _stop_discovery(self, args: dict) -> ToolResult:
        self._settings.set("discovery_enabled", False)
        self._settings.set("discovery_stop_at", "")
        return self._get_discovery_status({})

    def _get_discovery_status(self, args: dict) -> ToolResult:
        return ToolResult(content=json.dumps({
            "enabled": self._settings.get("discovery_enabled"),
            "stop_at": self._settings.get("discovery_stop_at"),
            "queries": self._settings.get("discovery_queries"),
            "interval": self._settings.get("discovery_interval"),
            "candidate_limit": self._settings.get("discovery_candidate_limit"),
            # Proves the background scheduler loop is alive at all, separate
            # from whether discovery itself found anything due -- see
            # scheduler_heartbeat's comment in settings.py.
            "scheduler_heartbeat": self._settings.get("scheduler_heartbeat"),
        }))

    # ------------------------------------------------------------------
    # 2026-08-26: book ingestion -- a book is just another idea SOURCE,
    # everything downstream (judge/screen/dispatch) reuses process_idea
    # unchanged (decision #33).
    # ------------------------------------------------------------------

    async def _upload_book(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        filename = (args.get("filename") or "").strip()
        data_b64 = args.get("data_base64") or ""
        if not filename or not data_b64:
            return ToolResult(content="filename and data_base64 are required", is_error=True)
        try:
            data = base64.b64decode(data_b64)
        except Exception as exc:
            return ToolResult(content=f"Invalid base64 data: {exc}", is_error=True)

        title = (args.get("title") or "").strip() or Path(filename).stem
        safe_name = Path(filename).name or "upload"

        dest_dir = self._books_dir / uuid.uuid4().hex
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        dest.write_bytes(data)

        text = extract_full_text(dest)
        if not text.strip():
            return ToolResult(
                content=(
                    f"Could not extract any text from '{filename}' -- supported "
                    "formats are PDF, EPUB, MOBI/AZW3, DOCX/DOC/ODT/RTF, and "
                    "plain text/Markdown."
                ),
                is_error=True,
            )

        book = self._book_store.add(title, safe_name, str(dest))
        chunks = chunk_text(text)
        self._book_store.set_total_chunks(book.id, len(chunks))
        self._launch_book_ingestion(book.id, chunks, title, strategy_store=strategy_store, fetch=fetch)

        return ToolResult(content=json.dumps({
            "book_id": book.id, "title": title, "status": "queued", "total_chunks": len(chunks),
        }))

    def _launch_book_ingestion(
        self, book_id: int, chunks: list, title: str, *, strategy_store=None, fetch=None,
    ) -> None:
        """Shared by upload_book and retry_book: fires the background
        ingestion task and registers it so stop_book/retry_book/
        delete_book can find and cancel it later."""
        task = asyncio.create_task(
            self._run_book_ingestion(book_id, chunks, title, strategy_store=strategy_store, fetch=fetch)
        )
        self._book_tasks[book_id] = task

    async def _run_book_ingestion(
        self, book_id: int, chunks: list, title: str, *, strategy_store=None, fetch=None,
    ) -> None:
        """Runs in the background (asyncio.create_task, not awaited by the
        upload_book caller) -- a real book is many LLM passes and would
        block the IPC response for minutes otherwise. BookStore progress
        is polled by list_books, not pushed."""
        async def run_gauntlet_fn(idea: Idea, ticker: str) -> dict:
            gauntlet_args = {
                "symbol": ticker,
                "hypothesis": idea.claim_text or "book-sourced hypothesis",
                "provenance": idea.provenance,
            }
            # _run_gauntlet requires one of code/claim/url/book+chapter to
            # generate strategy code from -- a book-sourced idea carries
            # book_info (set by from_book_claim), not source_url like a
            # web-sourced one. claim is also passed so the real extracted
            # claim text survives (book+chapter alone would fall back to a
            # generic "Hypothesis from {book}" -- see _run_gauntlet's own
            # book+chapter branch).
            if idea.book_info:
                gauntlet_args["book"] = idea.book_info.get("book", "")
                gauntlet_args["chapter"] = idea.book_info.get("chapter", "")
                gauntlet_args["claim"] = idea.claim_text
            result = await self._run_gauntlet(
                # "discovered", not a new "book" bucket -- origin is a
                # deliberately closed enum (strategy_store._VALID_ORIGINS)
                # and book ingestion is autonomous sourcing exactly like
                # web discovery, just from a different source. The book/
                # chapter provenance still survives via idea.provenance.
                gauntlet_args, origin="discovered", strategy_store=strategy_store, fetch=fetch,
            )
            return {"ticker": ticker, "is_error": result.is_error, "result": result.content}

        async def claim_extractor(chunk: str) -> list:
            return await extract_claims_from_chunk(chunk, self._router)

        async def judge_idea_fn(idea: Idea) -> "tuple[bool, str]":
            return await _judge_idea(idea, router=self._router)

        def rank_fn(symbols: list) -> list:
            fetch_fn = fetch
            if fetch_fn is None:
                from cerebral.trading_data import fetch_ohlcv as fetch_fn
            return rank_for_day_trading(symbols, fetch_fn)

        async def record_attempt_fn(entry: dict) -> None:
            self._discovery_attempts.record(
                entry["symbol"], entry["verdict"],
                reason=entry.get("reason", ""), idea_url=entry.get("idea_url", ""),
            )

        def on_progress(done: int, total: int, dispatched: int) -> None:
            self._book_store.update_progress(book_id, done, dispatched)
            if self._on_trading_change is not None:
                self._on_trading_change()

        # Every terminal-state write below is guarded by "am I still the
        # task registered for this book_id" -- retry_book cancels this
        # task and immediately registers a NEW one under the same id.
        # Cancellation is delivered asynchronously (at this coroutine's
        # next await, not synchronously in .cancel()), so without the
        # guard a stale cancelled run finishing its handler AFTER the new
        # run has already started would clobber fresh progress/state with
        # its own stale STOPPED/error write.
        current_task = asyncio.current_task()

        def _still_current() -> bool:
            return self._book_tasks.get(book_id) is current_task

        candidate_limit = self._settings.get("discovery_candidate_limit")
        try:
            await ingest_book(
                chunks, title, self._discovery_watchlist, run_gauntlet_fn, claim_extractor,
                judge_idea_fn=judge_idea_fn, record_activity_fn=self._record_activity_fn,
                record_attempt_fn=record_attempt_fn, rank_fn=rank_fn,
                candidate_limit=candidate_limit, on_progress=on_progress,
            )
            if _still_current():
                self._book_store.set_done(book_id)
        except asyncio.CancelledError:
            # Deliberately not re-raised: this coroutine IS the task
            # itself (fire-and-forget via asyncio.create_task) -- nothing
            # awaits its result or checks task.cancelled(), so swallowing
            # here just ends the task cleanly with a real terminal status
            # instead of leaving the book stuck at "processing" forever.
            if _still_current():
                self._book_store.set_stopped(book_id)
        except Exception as exc:
            logger.exception("[scheduler] book ingestion failed for book_id=%s", book_id)
            if _still_current():
                self._book_store.set_error(book_id, str(exc))
        finally:
            if _still_current():
                self._book_tasks.pop(book_id, None)
            if self._on_trading_change is not None:
                self._on_trading_change()

    def _list_books(self, args: dict, *, strategy_store=None) -> ToolResult:
        books = self._book_store.list_all()
        store = strategy_store if strategy_store is not None else StrategyStore()
        return ToolResult(content=json.dumps([
            {
                "id": b.id, "title": b.title, "filename": b.filename, "status": b.status,
                "total_chunks": b.total_chunks, "processed_chunks": b.processed_chunks,
                "strategies_found": b.strategies_found, "created_at": b.created_at,
                "error_message": b.error_message,
                "valid_strategies": list_validated_strategies(b.title, store),
            }
            for b in books
        ]))

    def _stop_book(self, args: dict) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)

        task = self._book_tasks.get(book_id)
        if task is not None and not task.done():
            task.cancel()  # _run_book_ingestion's own handler sets STATUS_STOPPED
        else:
            # No live task -- e.g. a Cerebral restart mid-run orphaned this
            # book at "processing" with nothing left to cancel. Mark it
            # stopped directly so it isn't stuck forever.
            self._book_store.set_stopped(book_id)
            if self._on_trading_change is not None:
                self._on_trading_change()
        return ToolResult(content=json.dumps({"book_id": book_id, "status": "stopped"}))

    async def _retry_book(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)

        task = self._book_tasks.get(book_id)
        if task is not None and not task.done():
            task.cancel()

        path = Path(book.stored_path)
        if not path.exists():
            return ToolResult(
                content=f"Original file for '{book.title}' is no longer on disk -- cannot redo",
                is_error=True,
            )
        text = extract_full_text(path)
        if not text.strip():
            return ToolResult(content=f"Could not re-extract any text from '{book.title}'", is_error=True)

        chunks = chunk_text(text)
        self._book_store.reset(book_id)
        self._book_store.set_total_chunks(book_id, len(chunks))
        self._launch_book_ingestion(book_id, chunks, book.title, strategy_store=strategy_store, fetch=fetch)
        if self._on_trading_change is not None:
            self._on_trading_change()

        return ToolResult(content=json.dumps({
            "book_id": book_id, "title": book.title, "status": "queued", "total_chunks": len(chunks),
        }))

    def _delete_book(self, args: dict) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)

        task = self._book_tasks.get(book_id)
        if task is not None and not task.done():
            task.cancel()

        try:
            stored_dir = Path(book.stored_path).resolve().parent
            if stored_dir.is_relative_to(self._books_dir.resolve()):
                shutil.rmtree(stored_dir, ignore_errors=True)
        except Exception:
            logger.warning("[scheduler] could not remove stored file for book_id=%s", book_id, exc_info=True)

        self._book_store.delete(book_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"book_id": book_id, "status": "deleted"}))

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

    def _run_paper_strategy(
        self, strategy_name: str, broker, forward_record: "ForwardRecord",
        config: dict | None = None, store=None, fetch=None, phase: str = "paper",
        dispatch_id: str | None = None,
        risk=None, size_pct: float = 1.0,  # S20
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
