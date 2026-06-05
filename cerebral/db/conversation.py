"""Conversation store — the fifth memory tier (ADR-0007, Issue #185).

Persists per-profile chat turns to the same SQLite database as profiles /
queue / ACL. Mirrors the existing ``CredentialStore`` / ``ProfileManager``
shape: one class, owns its connection, ``_init_schema`` runs idempotent
``CREATE TABLE IF NOT EXISTS`` at construct time so a fresh DB and a
migrated DB both end up valid.

Retention is infinite in v1 (ADR-0007 / privacy debt acknowledged); the
manual-purge UX is a v2 deepening. Raw audio is never written -- only
post-Whisper text and Felix's response text.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DB_PATH = Path(__file__).parent.parent / "data" / "openmind.db"

# Closed kind vocabulary (ADR-0007). The Main window renderer treats any
# unknown kind as ``system_event`` so a forward-rolling Cerebral with a
# new kind doesn't crash an old renderer.
KIND_USER_VOICE   = "user_voice"
KIND_USER_TEXT    = "user_text"
KIND_FELIX_SPEECH = "felix_speech"
KIND_TOOL_CALL    = "tool_call"
KIND_TOOL_RESULT  = "tool_result"
KIND_SYSTEM_EVENT = "system_event"

VALID_KINDS = frozenset({
    KIND_USER_VOICE,
    KIND_USER_TEXT,
    KIND_FELIX_SPEECH,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    KIND_SYSTEM_EVENT,
})


@dataclass
class ConversationTurn:
    id: int
    profile_id: int
    ts: str
    kind: str
    content: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "ts": self.ts,
            "kind": self.kind,
            "content": self.content,
        }


class ConversationStore:
    """Per-profile transcript persistence.

    One instance per Cerebral process; the active-profile filter is a
    per-call argument so a profile switch doesn't require a rebuild.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id   INTEGER NOT NULL,
                ts           DATETIME DEFAULT CURRENT_TIMESTAMP,
                kind         TEXT    NOT NULL,
                content_json TEXT    NOT NULL DEFAULT '{}',
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_turns_profile_ts
                ON conversation_turns (profile_id, ts);
        """)
        self._con.commit()

    def append(self, profile_id: int, kind: str, content: dict) -> ConversationTurn:
        """Record one turn. Returns the persisted row (id + ts populated)."""
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown conversation kind: {kind!r}")
        payload = json.dumps(content or {}, ensure_ascii=False)
        cur = self._con.execute(
            "INSERT INTO conversation_turns (profile_id, kind, content_json) "
            "VALUES (?, ?, ?)",
            (profile_id, kind, payload),
        )
        self._con.commit()
        return self._get(cur.lastrowid)  # type: ignore[arg-type]

    def list_recent(self, profile_id: int, limit: int = 50) -> list[ConversationTurn]:
        """Return the last ``limit`` turns for a profile, oldest first.

        Oldest-first ordering matches transcript reading order; the
        renderer scrolls to the bottom (newest) on initial load.
        """
        if limit <= 0:
            return []
        rows = self._con.execute(
            "SELECT * FROM (SELECT * FROM conversation_turns "
            "WHERE profile_id=? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id ASC",
            (profile_id, limit),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def _get(self, turn_id: int) -> ConversationTurn:
        row = self._con.execute(
            "SELECT * FROM conversation_turns WHERE id=?", (turn_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"conversation turn {turn_id} not found")
        return _row_to_turn(row)

    def purge(self, profile_id: int) -> int:
        """Delete every turn for ``profile_id``. Returns rows deleted.

        Wired but currently unused -- the manual-purge UX is the v2
        deepening per ADR-0007. Provided so a SQL-savvy user can call it
        from the REPL today, and so the v2 surface has no schema work
        ahead of it."""
        cur = self._con.execute(
            "DELETE FROM conversation_turns WHERE profile_id=?", (profile_id,)
        )
        self._con.commit()
        return cur.rowcount


def _row_to_turn(row: sqlite3.Row) -> ConversationTurn:
    try:
        content = json.loads(row["content_json"]) if row["content_json"] else {}
    except (ValueError, TypeError):
        content = {}
    return ConversationTurn(
        id=row["id"],
        profile_id=row["profile_id"],
        ts=row["ts"],
        kind=row["kind"],
        content=content,
    )
