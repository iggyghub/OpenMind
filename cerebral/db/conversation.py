"""Conversation store -- the fifth memory tier (ADR-0007, Issue #185).

Persists per-profile chat turns to the same SQLite database as profiles /
queue / ACL. Mirrors the existing ``CredentialStore`` / ``ProfileManager``
shape: one class, owns its connection, ``_init_schema`` runs idempotent
``CREATE TABLE IF NOT EXISTS`` at construct time so a fresh DB and a
migrated DB both end up valid.

S9 (#292) adds ``conversation_threads`` and a ``thread_id`` column on
``conversation_turns``. Existing turns are back-filled into one "Legacy"
thread per profile so the migration is non-destructive (ADR-0007).

S11 (#294) adds ``conversation_projects`` and a ``project_id`` column on
``conversation_threads``. "Unfiled" = ``project_id IS NULL``; deleting a
project sets its threads' ``project_id`` to NULL via ``ON DELETE SET NULL``
so we never lose conversation history when a folder is removed.

Retention is infinite in v1 (ADR-0007 / privacy debt acknowledged); the
manual-purge UX is a v2 deepening. Raw audio is never written -- only
post-Whisper text and Felix's response text.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cerebral.paths import data_dir
from cerebral.db import crypto

DB_PATH = data_dir() / "openmind.db"

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

# S9 (#292) -- the back-fill thread for pre-#292 turns. Stable name so a
# user with a partly-migrated DB doesn't end up with two of them.
_LEGACY_THREAD_TITLE = "Legacy conversation"

# Cap on the auto-generated title derived from the first user turn.
_AUTO_TITLE_MAX_CHARS = 60


@dataclass
class ConversationTurn:
    id: int
    profile_id: int
    ts: str
    kind: str
    content: dict
    thread_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "thread_id": self.thread_id,
            "ts": self.ts,
            "kind": self.kind,
            "content": self.content,
        }


@dataclass
class ConversationThread:
    id: int
    profile_id: int
    title: str
    created_at: str
    updated_at: str
    project_id: int | None = None
    model_override: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_id": self.project_id,
            "model_override": self.model_override,
        }


@dataclass
class ConversationProject:
    id: int
    profile_id: int
    name: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "name": self.name,
            "created_at": self.created_at,
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
        # Order matters on a pre-S9 DB: the existing conversation_turns
        # table doesn't have a thread_id column yet, so CREATE TABLE IF
        # NOT EXISTS is a no-op AND the thread_id index would fail.
        # Sequence: tables (no thread_id index) -> ALTER ADD COLUMN ->
        # backfill -> index that references thread_id.
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id  INTEGER NOT NULL,
                name        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_projects_profile
                ON conversation_projects (profile_id);

            CREATE TABLE IF NOT EXISTS conversation_threads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id  INTEGER NOT NULL,
                title       TEXT    NOT NULL DEFAULT '',
                -- ms-resolution timestamps so two threads created (or
                -- two updates landing) within the same wall second sort
                -- in the correct insertion order. CURRENT_TIMESTAMP is
                -- only second-granular and would tie-break to a stale
                -- "most recent" thread on rapid creates.
                created_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                updated_at  TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                project_id  INTEGER,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY (project_id) REFERENCES conversation_projects(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_threads_profile_updated
                ON conversation_threads (profile_id, updated_at);

            CREATE TABLE IF NOT EXISTS conversation_turns (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id   INTEGER NOT NULL,
                thread_id    INTEGER,
                ts           DATETIME DEFAULT CURRENT_TIMESTAMP,
                kind         TEXT    NOT NULL,
                content_json TEXT    NOT NULL DEFAULT '{}',
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY (thread_id)  REFERENCES conversation_threads(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_turns_profile_ts
                ON conversation_turns (profile_id, ts);
        """)
        self._con.commit()
        # S9 migration: pre-#292 DBs lack the thread_id column.
        try:
            self._con.execute(
                "ALTER TABLE conversation_turns ADD COLUMN thread_id INTEGER"
            )
            self._con.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        # Now thread_id is guaranteed to exist -- safe to index.
        self._con.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_thread_ts "
            "ON conversation_turns (thread_id, ts)"
        )
        self._con.commit()
        # S11 migration: pre-#294 DBs lack the project_id column on threads.
        try:
            self._con.execute(
                "ALTER TABLE conversation_threads ADD COLUMN project_id INTEGER"
            )
            self._con.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        self._con.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_threads_project "
            "ON conversation_threads (project_id)"
        )
        self._con.commit()
        # S13 migration: pre-#296 DBs lack the model_override column on threads.
        try:
            self._con.execute(
                "ALTER TABLE conversation_threads ADD COLUMN model_override TEXT"
            )
            self._con.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        self._backfill_legacy_threads()

    def _backfill_legacy_threads(self) -> None:
        """Migrate pre-#292 turns into one "Legacy" thread per profile.

        Non-destructive (ADR-0007 retention contract): we never delete or
        rewrite content, only fill the new thread_id column. Idempotent --
        a second call is a no-op because by then no rows have NULL thread_id.
        """
        rows = self._con.execute(
            "SELECT DISTINCT profile_id FROM conversation_turns "
            "WHERE thread_id IS NULL"
        ).fetchall()
        for row in rows:
            profile_id = row["profile_id"]
            existing = self._con.execute(
                "SELECT id FROM conversation_threads "
                "WHERE profile_id = ? AND title = ? ORDER BY id ASC LIMIT 1",
                (profile_id, _LEGACY_THREAD_TITLE),
            ).fetchone()
            if existing is not None:
                thread_id = existing["id"]
            else:
                cur = self._con.execute(
                    "INSERT INTO conversation_threads (profile_id, title) "
                    "VALUES (?, ?)",
                    (profile_id, _LEGACY_THREAD_TITLE),
                )
                thread_id = cur.lastrowid
            self._con.execute(
                "UPDATE conversation_turns SET thread_id = ? "
                "WHERE profile_id = ? AND thread_id IS NULL",
                (thread_id, profile_id),
            )
        if rows:
            self._con.commit()

    # -- threads ----------------------------------------------------------

    def create_thread(self, profile_id: int, title: str = "") -> ConversationThread:
        cur = self._con.execute(
            "INSERT INTO conversation_threads (profile_id, title) VALUES (?, ?)",
            (profile_id, title or ""),
        )
        self._con.commit()
        return self._get_thread(cur.lastrowid)  # type: ignore[arg-type]

    def list_threads(self, profile_id: int) -> list[ConversationThread]:
        """All threads for a profile, most-recently-updated first."""
        rows = self._con.execute(
            "SELECT * FROM conversation_threads "
            "WHERE profile_id = ? ORDER BY updated_at DESC, id DESC",
            (profile_id,),
        ).fetchall()
        return [_row_to_thread(r) for r in rows]

    def get_thread(self, thread_id: int) -> ConversationThread | None:
        row = self._con.execute(
            "SELECT * FROM conversation_threads WHERE id = ?", (thread_id,)
        ).fetchone()
        return _row_to_thread(row) if row else None

    def rename_thread(self, thread_id: int, title: str) -> bool:
        """Set a thread's title (user edit OR auto-title). Empty string allowed
        so the auto-title pass can re-derive on the next felix exchange."""
        cur = self._con.execute(
            "UPDATE conversation_threads SET title = ?, "
            "updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now') WHERE id = ?",
            (title or "", thread_id),
        )
        self._con.commit()
        return cur.rowcount > 0

    def latest_thread(self, profile_id: int) -> ConversationThread | None:
        row = self._con.execute(
            "SELECT * FROM conversation_threads "
            "WHERE profile_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        return _row_to_thread(row) if row else None

    def get_or_create_default_thread(self, profile_id: int) -> ConversationThread:
        """Return the profile's most-recent thread, creating an empty one if
        none exists. Used by ``append`` so a caller that doesn't yet know
        about thread_id (legacy callers, direct test usage) still produces
        consistent rows."""
        thread = self.latest_thread(profile_id)
        if thread is not None:
            return thread
        return self.create_thread(profile_id, title="")

    # -- turns ------------------------------------------------------------

    def append(
        self,
        profile_id: int,
        kind: str,
        content: dict,
        *,
        thread_id: int | None = None,
    ) -> ConversationTurn:
        """Record one turn. Returns the persisted row (id + ts populated).

        When ``thread_id`` is omitted, the turn is attached to the profile's
        most recent thread (one is auto-created if the profile has none).
        Auto-titles untitled threads from the first user turn after their
        first felix exchange (S9 / #292).
        """
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown conversation kind: {kind!r}")
        if thread_id is None:
            thread = self.get_or_create_default_thread(profile_id)
            thread_id = thread.id
        payload = json.dumps(content or {}, ensure_ascii=False)
        payload = crypto.encrypt(payload)
        cur = self._con.execute(
            "INSERT INTO conversation_turns "
            "(profile_id, thread_id, kind, content_json) VALUES (?, ?, ?, ?)",
            (profile_id, thread_id, kind, payload),
        )
        self._con.execute(
            "UPDATE conversation_threads SET updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now') "
            "WHERE id = ?",
            (thread_id,),
        )
        self._con.commit()
        turn = self._get(cur.lastrowid)  # type: ignore[arg-type]
        self._maybe_auto_title(thread_id, kind)
        return turn

    def _maybe_auto_title(self, thread_id: int, kind: str) -> None:
        """When a thread is still untitled and Felix just spoke, derive a
        title from the first user turn in that thread. Spec: "auto-title
        from the first exchange (editable)" -- the felix turn is our
        signal that an exchange happened."""
        if kind != KIND_FELIX_SPEECH:
            return
        thread = self.get_thread(thread_id)
        if thread is None or (thread.title or "").strip():
            return
        row = self._con.execute(
            "SELECT content_json FROM conversation_turns "
            "WHERE thread_id = ? AND kind IN (?, ?) "
            "ORDER BY id ASC LIMIT 1",
            (thread_id, KIND_USER_TEXT, KIND_USER_VOICE),
        ).fetchone()
        if row is None:
            return
        try:
            content = json.loads(row["content_json"]) if row["content_json"] else {}
        except (ValueError, TypeError):
            content = {}
        text = (content.get("text") or "").strip()
        if not text:
            return
        title = text[:_AUTO_TITLE_MAX_CHARS].rstrip()
        if len(text) > _AUTO_TITLE_MAX_CHARS:
            title = title + "..."
        self._con.execute(
            "UPDATE conversation_threads SET title = ? WHERE id = ?",
            (title, thread_id),
        )
        self._con.commit()

    def list_recent(self, profile_id: int, limit: int = 50) -> list[ConversationTurn]:
        """Return the last ``limit`` turns for a profile, oldest first.

        Profile-scoped (cross-thread). Oldest-first ordering matches
        transcript reading order; the renderer scrolls to the bottom
        (newest) on initial load.
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

    def list_recent_for_thread(
        self, thread_id: int, limit: int = 50
    ) -> list[ConversationTurn]:
        """Like ``list_recent`` but scoped to one thread (S9)."""
        if limit <= 0:
            return []
        rows = self._con.execute(
            "SELECT * FROM (SELECT * FROM conversation_turns "
            "WHERE thread_id=? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id ASC",
            (thread_id, limit),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def _get(self, turn_id: int) -> ConversationTurn:
        row = self._con.execute(
            "SELECT * FROM conversation_turns WHERE id=?", (turn_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"conversation turn {turn_id} not found")
        return _row_to_turn(row)

    def _get_thread(self, thread_id: int) -> ConversationThread:
        row = self._con.execute(
            "SELECT * FROM conversation_threads WHERE id=?", (thread_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"conversation thread {thread_id} not found")
        return _row_to_thread(row)

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

    def delete_thread(self, thread_id: int) -> bool:
        """Delete a thread and all its turns. Returns True if the thread existed.

        Turns carry ``ON DELETE SET NULL`` on thread_id in the schema, so we
        delete them explicitly before dropping the thread to satisfy the
        S10 spec requirement that deleting purges turns."""
        self._con.execute(
            "DELETE FROM conversation_turns WHERE thread_id = ?", (thread_id,)
        )
        cur = self._con.execute(
            "DELETE FROM conversation_threads WHERE id = ?", (thread_id,)
        )
        self._con.commit()
        return cur.rowcount > 0

    def list_threads_with_counts(self, profile_id: int) -> list[dict]:
        """Like ``list_threads`` but each dict also carries ``turn_count``."""
        rows = self._con.execute(
            """
            SELECT ct.id, ct.profile_id, ct.title, ct.created_at, ct.updated_at,
                   ct.project_id, ct.model_override,
                   COUNT(ctu.id) AS turn_count
            FROM conversation_threads ct
            LEFT JOIN conversation_turns ctu ON ctu.thread_id = ct.id
            WHERE ct.profile_id = ?
            GROUP BY ct.id
            ORDER BY ct.updated_at DESC, ct.id DESC
            """,
            (profile_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = _row_to_thread(r).to_dict()
            d["turn_count"] = r["turn_count"]
            result.append(d)
        return result

    def search_threads(
        self, profile_id: int, query: str, limit: int = 20
    ) -> list[dict]:
        """Search threads by title and turn content. Returns dicts with turn_count.

        Searches ``content_json`` as raw text so it catches any key/value
        that contains the query string without needing full-text indexing."""
        q = f"%{query}%"
        rows = self._con.execute(
            """
            SELECT DISTINCT ct.id, ct.profile_id, ct.title,
                   ct.created_at, ct.updated_at, ct.project_id, ct.model_override,
                   (SELECT COUNT(*) FROM conversation_turns
                    WHERE thread_id = ct.id) AS turn_count
            FROM conversation_threads ct
            LEFT JOIN conversation_turns ctu ON ctu.thread_id = ct.id
            WHERE ct.profile_id = ?
              AND (ct.title LIKE ? OR ctu.content_json LIKE ?)
            ORDER BY ct.updated_at DESC, ct.id DESC
            LIMIT ?
            """,
            (profile_id, q, q, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = _row_to_thread(r).to_dict()
            d["turn_count"] = r["turn_count"]
            result.append(d)
        return result

    # -- projects (S11 / #294) -------------------------------------------

    def create_project(self, profile_id: int, name: str = "") -> ConversationProject:
        """Create a new project folder for the profile. Empty names are allowed
        so the UI can hand back a fresh row for inline rename."""
        cur = self._con.execute(
            "INSERT INTO conversation_projects (profile_id, name) VALUES (?, ?)",
            (profile_id, (name or "").strip()),
        )
        self._con.commit()
        return self._get_project(cur.lastrowid)  # type: ignore[arg-type]

    def list_projects(self, profile_id: int) -> list[ConversationProject]:
        """All projects for a profile, oldest-first so the order is stable
        across reloads (created_at is a string but lexicographically sorts
        by time at ms resolution)."""
        rows = self._con.execute(
            "SELECT * FROM conversation_projects "
            "WHERE profile_id = ? ORDER BY created_at ASC, id ASC",
            (profile_id,),
        ).fetchall()
        return [_row_to_project(r) for r in rows]

    def list_projects_with_counts(self, profile_id: int) -> list[dict]:
        """Like ``list_projects`` but each dict carries ``thread_count``."""
        rows = self._con.execute(
            """
            SELECT cp.id, cp.profile_id, cp.name, cp.created_at,
                   COUNT(ct.id) AS thread_count
            FROM conversation_projects cp
            LEFT JOIN conversation_threads ct ON ct.project_id = cp.id
            WHERE cp.profile_id = ?
            GROUP BY cp.id
            ORDER BY cp.created_at ASC, cp.id ASC
            """,
            (profile_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = _row_to_project(r).to_dict()
            d["thread_count"] = r["thread_count"]
            result.append(d)
        return result

    def get_project(self, project_id: int) -> ConversationProject | None:
        row = self._con.execute(
            "SELECT * FROM conversation_projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _row_to_project(row) if row else None

    def _get_project(self, project_id: int) -> ConversationProject:
        row = self._con.execute(
            "SELECT * FROM conversation_projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"conversation project {project_id} not found")
        return _row_to_project(row)

    def rename_project(self, project_id: int, name: str) -> bool:
        cur = self._con.execute(
            "UPDATE conversation_projects SET name = ? WHERE id = ?",
            ((name or "").strip(), project_id),
        )
        self._con.commit()
        return cur.rowcount > 0

    def delete_project(self, project_id: int) -> bool:
        """Delete a project. Threads inside are NOT deleted -- their
        ``project_id`` is set to NULL so they fall back into "Unfiled"
        (S11 acceptance criterion). Done manually rather than relying on
        the ON DELETE SET NULL FK so the store works regardless of the
        connection's foreign_keys pragma."""
        self._con.execute(
            "UPDATE conversation_threads SET project_id = NULL "
            "WHERE project_id = ?",
            (project_id,),
        )
        cur = self._con.execute(
            "DELETE FROM conversation_projects WHERE id = ?", (project_id,)
        )
        self._con.commit()
        return cur.rowcount > 0

    def set_thread_model_override(self, thread_id: int, model_id: str | None) -> bool:
        """Pin or clear the per-thread model override (S13 / #296).

        Pass model_id=None to clear the override and fall back to the
        global active model. Returns True if the thread was found."""
        cur = self._con.execute(
            "UPDATE conversation_threads SET model_override = ? WHERE id = ?",
            (model_id, thread_id),
        )
        self._con.commit()
        return cur.rowcount > 0

    def move_thread_to_project(
        self, thread_id: int, project_id: int | None
    ) -> bool:
        """Reassign a thread to a project (or to Unfiled when ``project_id``
        is None). Updates the thread's ``updated_at`` so it floats to the
        top of the project list, mirroring "user just touched this"."""
        cur = self._con.execute(
            "UPDATE conversation_threads SET project_id = ?, "
            "updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now') WHERE id = ?",
            (project_id, thread_id),
        )
        self._con.commit()
        return cur.rowcount > 0


def _row_to_turn(row: sqlite3.Row) -> ConversationTurn:
    try:
        content = json.loads(row["content_json"]) if row["content_json"] else {}
    except (ValueError, TypeError):
        content = {}
    # ``thread_id`` is nullable in case a legacy migration hasn't run yet.
    try:
        thread_id = row["thread_id"]
    except (IndexError, KeyError):
        thread_id = None
    return ConversationTurn(
        id=row["id"],
        profile_id=row["profile_id"],
        ts=row["ts"],
        kind=row["kind"],
        content=content,
        thread_id=thread_id,
    )


def _row_to_thread(row: sqlite3.Row) -> ConversationThread:
    # ``project_id`` / ``model_override`` may be absent on rows from queries
    # that pre-date those columns; tolerate so older fetch sites keep working.
    try:
        project_id = row["project_id"]
    except (IndexError, KeyError):
        project_id = None
    try:
        model_override = row["model_override"]
    except (IndexError, KeyError):
        model_override = None
    return ConversationThread(
        id=row["id"],
        profile_id=row["profile_id"],
        title=row["title"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        project_id=project_id,
        model_override=model_override,
    )


def _row_to_project(row: sqlite3.Row) -> ConversationProject:
    return ConversationProject(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"] or "",
        created_at=row["created_at"] or "",
    )
