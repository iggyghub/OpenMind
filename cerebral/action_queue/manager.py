"""
Queue manager — Issue #8.

Stores candidate actions Felix has identified but not yet executed.
Backed by SQLite so items survive tray restarts.

Public interface:
  mgr = QueueManager()                            # uses cerebral/data/openmind.db
  mgr = QueueManager(":memory:")                  # tests
  item = mgr.add_item("title", "summary")         # → QueueItem (status="pending")
  item = mgr.add_item("title", "s", tool_name="set_alarm", tool_args={"time":"07:00"})
  item = mgr.approve_item(item.id)                # → QueueItem | None
  ok   = mgr.dismiss_item(item.id)                # → bool
  items = mgr.get_pending()                       # → list[QueueItem]
  mgr.clear_all()
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "openmind.db"


@dataclass
class QueueItem:
    id: str
    title: str
    summary: str
    status: str                         # "pending" | "approved" | "dismissed"
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = field(default=None)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "created_at": self.created_at,
        }


class QueueManager:
    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS queue (
                id          TEXT    PRIMARY KEY,
                title       TEXT    NOT NULL,
                summary     TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'pending',
                tool_name   TEXT,
                tool_args   TEXT,
                created_at  TEXT    NOT NULL
            );
        """)
        self._con.commit()

    # ── writes ────────────────────────────────────────────────────────────────

    def add_item(
        self,
        title: str,
        summary: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[dict] = None,
    ) -> QueueItem:
        item_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        args_json = json.dumps(tool_args) if tool_args is not None else None
        self._con.execute(
            "INSERT INTO queue (id, title, summary, status, tool_name, tool_args, created_at)"
            " VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (item_id, title, summary, tool_name, args_json, created_at),
        )
        self._con.commit()
        return QueueItem(
            id=item_id,
            title=title,
            summary=summary,
            status="pending",
            tool_name=tool_name,
            tool_args=tool_args,
            created_at=created_at,
        )

    def approve_item(self, item_id: str) -> Optional[QueueItem]:
        cur = self._con.execute(
            "UPDATE queue SET status='approved' WHERE id=? AND status='pending'",
            (item_id,),
        )
        self._con.commit()
        if cur.rowcount == 0:
            return None
        return self._fetch(item_id)

    def dismiss_item(self, item_id: str) -> bool:
        cur = self._con.execute(
            "UPDATE queue SET status='dismissed' WHERE id=? AND status='pending'",
            (item_id,),
        )
        self._con.commit()
        return cur.rowcount > 0

    def clear_all(self) -> None:
        self._con.execute("DELETE FROM queue")
        self._con.commit()

    # ── reads ─────────────────────────────────────────────────────────────────

    def get_pending(self) -> list[QueueItem]:
        rows = self._con.execute(
            "SELECT * FROM queue WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_item(self, item_id: str) -> Optional[QueueItem]:
        row = self._con.execute(
            "SELECT * FROM queue WHERE id=?", (item_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def _fetch(self, item_id: str) -> Optional[QueueItem]:
        return self.get_item(item_id)

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> QueueItem:
        raw_args = row["tool_args"]
        return QueueItem(
            id=row["id"],
            title=row["title"],
            summary=row["summary"],
            status=row["status"],
            tool_name=row["tool_name"],
            tool_args=json.loads(raw_args) if raw_args else None,
            created_at=row["created_at"],
        )
