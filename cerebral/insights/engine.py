"""
Insights engine — Issue #13.

Passive preference learning: every approve/dismiss of a queue item is a signal.
When a pattern reaches PATTERN_THRESHOLD, an Insight record is auto-created.
Insights are per-profile, editable, deletable, and pinnable.

Public interface:
  eng = InsightsEngine(profile_id=1)                   # production
  eng = InsightsEngine(1, db_path=":memory:")           # tests

  eng.record_signal("approve", "Set alarm", tool_name="set_alarm")
  insight = eng.maybe_create_insight("Set alarm", tool_name="set_alarm")  # None until threshold

  insights = eng.list_insights()          # → list[Insight]
  eng.delete_insight(insight.id)          # → bool
  eng.pin_insight(insight.id)             # → bool  (toggles)
  eng.edit_insight(insight.id, "Custom")  # → bool
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "openmind.db"


@dataclass
class Insight:
    id: str
    profile_id: int
    description: str
    example: str
    pinned: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "description": self.description,
            "example": self.example,
            "pinned": self.pinned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class InsightsEngine:
    PATTERN_THRESHOLD: int = 3

    def __init__(self, profile_id: int, db_path: str | Path = DB_PATH) -> None:
        self._profile_id = profile_id

        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS insight_signals (
                id          TEXT    PRIMARY KEY,
                profile_id  INTEGER NOT NULL,
                action      TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                tool_name   TEXT,
                pattern_key TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS insights (
                id          TEXT    PRIMARY KEY,
                profile_id  INTEGER NOT NULL,
                description TEXT    NOT NULL,
                example     TEXT    NOT NULL,
                pattern_key TEXT    NOT NULL,
                pinned      INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );
        """)
        self._con.commit()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _pattern_key(title: str, tool_name: Optional[str]) -> str:
        if tool_name:
            return tool_name.strip().lower()
        return title.strip().lower()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── signals ───────────────────────────────────────────────────────────────

    def record_signal(
        self, action: str, title: str, tool_name: Optional[str] = None
    ) -> None:
        key = self._pattern_key(title, tool_name)
        self._con.execute(
            "INSERT INTO insight_signals (id, profile_id, action, title, tool_name, pattern_key, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), self._profile_id, action, title, tool_name, key, self._now()),
        )
        self._con.commit()
        logger.debug("[insights] Signal recorded: %s %r (key=%s)", action, title, key)

    def maybe_create_insight(
        self, title: str, tool_name: Optional[str] = None
    ) -> Optional[Insight]:
        key = self._pattern_key(title, tool_name)

        # Already have an insight for this pattern?
        existing = self._con.execute(
            "SELECT id FROM insights WHERE profile_id=? AND pattern_key=?",
            (self._profile_id, key),
        ).fetchone()
        if existing:
            return None

        # Count signals for this pattern
        count = self._con.execute(
            "SELECT COUNT(*) FROM insight_signals WHERE profile_id=? AND pattern_key=?",
            (self._profile_id, key),
        ).fetchone()[0]

        if count < self.PATTERN_THRESHOLD:
            return None

        # Create the insight
        insight_id = str(uuid.uuid4())
        now = self._now()
        example = tool_name if tool_name else title.strip()
        description = f"Felix often handles '{example}' actions for you"

        self._con.execute(
            "INSERT INTO insights (id, profile_id, description, example, pattern_key, pinned, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (insight_id, self._profile_id, description, example, key, now, now),
        )
        self._con.commit()
        logger.info("[insights] New insight created for profile %d: %r", self._profile_id, description)

        return self._fetch(insight_id)

    # ── reads ─────────────────────────────────────────────────────────────────

    def list_insights(self) -> list[Insight]:
        rows = self._con.execute(
            "SELECT * FROM insights WHERE profile_id=? ORDER BY created_at ASC",
            (self._profile_id,),
        ).fetchall()
        return [self._row_to_insight(r) for r in rows]

    def _fetch(self, insight_id: str) -> Optional[Insight]:
        row = self._con.execute(
            "SELECT * FROM insights WHERE id=?", (insight_id,)
        ).fetchone()
        return self._row_to_insight(row) if row else None

    @staticmethod
    def _row_to_insight(row: sqlite3.Row) -> Insight:
        return Insight(
            id=row["id"],
            profile_id=row["profile_id"],
            description=row["description"],
            example=row["example"],
            pinned=bool(row["pinned"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── mutations ─────────────────────────────────────────────────────────────

    def delete_insight(self, insight_id: str) -> bool:
        cur = self._con.execute(
            "DELETE FROM insights WHERE id=? AND profile_id=?",
            (insight_id, self._profile_id),
        )
        self._con.commit()
        return cur.rowcount > 0

    def pin_insight(self, insight_id: str) -> bool:
        row = self._con.execute(
            "SELECT pinned FROM insights WHERE id=? AND profile_id=?",
            (insight_id, self._profile_id),
        ).fetchone()
        if not row:
            return False
        new_pinned = 0 if row["pinned"] else 1
        self._con.execute(
            "UPDATE insights SET pinned=?, updated_at=? WHERE id=?",
            (new_pinned, self._now(), insight_id),
        )
        self._con.commit()
        return True

    def edit_insight(self, insight_id: str, description: str) -> bool:
        cur = self._con.execute(
            "UPDATE insights SET description=?, updated_at=? WHERE id=? AND profile_id=?",
            (description, self._now(), insight_id, self._profile_id),
        )
        self._con.commit()
        return cur.rowcount > 0
