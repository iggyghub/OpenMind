"""
Scheduler plugin — MCP server for Felix.

Tools: create_event, list_events, update_event, delete_event.
SQLite-backed (same openmind.db). No external calendar deps.
"""
import json
import logging
import sqlite3
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "scheduler"

# ADR-0005 / Issue #44 — list_events reads SQLite (fs_read); create_event /
# update_event / delete_event mutate the events table (fs_write). The DB
# file itself is never unlinked, so fs_delete is not needed.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})

from cerebral.paths import data_dir

_DEFAULT_DB = data_dir() / "openmind.db"

_VALID_RECURRENCES = {"daily", "weekly", "monthly"}


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
        if recurrence and recurrence not in _VALID_RECURRENCES:
            return ToolResult(
                content=f"recurrence must be one of {sorted(_VALID_RECURRENCES)}", is_error=True
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

    def _run_paper_strategy(self, strategy_name: str, broker, forward_record: "ForwardRecord", config: dict | None = None) -> dict:
        """Executes a scheduled paper trade for the given strategy."""
        if not broker or not forward_record:
            return {"status": "skipped", "reason": "broker/record not provided"}
        
        try:
            order = broker.place_order(
                symbol=config.get("symbol", "SYMBOL"),
                qty=config.get("position_size", 1.0),
                side=config.get("side", "buy"),
                price=0.0,
                order_type="market"
            )
            if order.status == "FILLED":
                forward_record.add_fill(
                    symbol=order.symbol,
                    side=order.side,
                    qty=order.qty,
                    price=order.price,
                    fees=order.fees,
                    pnl=0.0,
                    phase="paper",
                    strategy_id=strategy_name
                )
                logger.info(f"Paper trade executed for {strategy_name}: {order.id}")
                return {"status": "executed", "order_id": order.id}
        except Exception as e:
            logger.warning(f"Paper trade execution failed for {strategy_name}: {e}")
            return {"status": "error", "reason": str(e)}
        return {"status": "skipped", "reason": "no signal"}


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
