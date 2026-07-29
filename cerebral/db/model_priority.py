"""
Per-profile model priority + enabled flags (P1 #531).

Stores the ordered list of ModelRouter ids the user has arranged in the priority
panel, and the per-model enabled flag. The master fallback toggle itself lives
on the profile row (see profiles.fallback_enabled) so it follows the local_only
kill-switch pattern.

  profile_id  the profile that owns this ordering
  model_id    ModelRouter id (e.g. "ollama/qwen3:8b", "claude/haiku", "custom/box")
  position    0-based rank; lower = higher priority (routed first)
  enabled     1 = participates in routing, 0 = hidden/skipped
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cerebral.paths import data_dir

DB_PATH = data_dir() / "openmind.db"


class ModelPriorityStore:
    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS model_priority (
                profile_id INTEGER NOT NULL,
                model_id   TEXT    NOT NULL,
                position   INTEGER NOT NULL,
                enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                PRIMARY KEY (profile_id, model_id),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
        """)
        self._con.commit()

    def save(
        self, profile_id: int, priority: list[str], enabled: dict[str, bool],
    ) -> None:
        """Replace the full priority + enabled snapshot for a profile."""
        with self._con:
            self._con.execute(
                "DELETE FROM model_priority WHERE profile_id=?", (profile_id,)
            )
            for pos, mid in enumerate(priority):
                self._con.execute(
                    """INSERT INTO model_priority
                           (profile_id, model_id, position, enabled)
                       VALUES (?, ?, ?, ?)""",
                    (profile_id, mid, pos, 1 if enabled.get(mid, True) else 0),
                )

    def load(self, profile_id: int) -> list[dict]:
        rows = self._con.execute(
            """SELECT model_id, position, enabled
                 FROM model_priority
                WHERE profile_id=?
                ORDER BY position""",
            (profile_id,),
        ).fetchall()
        return [
            {"model_id": r["model_id"], "position": r["position"],
             "enabled": bool(r["enabled"])}
            for r in rows
        ]
