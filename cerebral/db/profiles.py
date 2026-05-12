"""
Profile persistence via SQLite.

Each Profile stores the user's identity and preferences.
Settings table holds the active profile pointer.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "openmind.db"


@dataclass
class Profile:
    name: str
    wake_name: str = "felix"
    pronunciation_guide: str = ""
    voice_id: str = "default"
    connected_accounts: list = field(default_factory=list)
    voice_sample: str = ""   # base64 audio/webm — user saying their name (for TTS pronunciation)
    wake_sample:  str = ""   # base64 audio/webm — user saying the wake word (for Vosk tuning)
    active_model: str = ""   # last ModelRouter id chosen by the user (e.g. "ollama/gemma3:latest")
    # Per-profile snapshot of the system DEFAULT_POLICY at the time this
    # profile was created (Issue #45, ADR-0005). Subsequent changes to the
    # system defaults do not propagate; the profile keeps its frozen view.
    # Maps capability value-string → "silent" | "ask" | "deny".
    acl_defaults_snapshot: dict = field(default_factory=dict)
    id: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class ProfileManager:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                name                     TEXT    NOT NULL,
                wake_name                TEXT    NOT NULL DEFAULT 'felix',
                pronunciation_guide      TEXT    NOT NULL DEFAULT '',
                voice_id                 TEXT    NOT NULL DEFAULT 'default',
                connected_accounts       TEXT    NOT NULL DEFAULT '[]',
                voice_sample             TEXT    NOT NULL DEFAULT '',
                wake_sample              TEXT    NOT NULL DEFAULT '',
                active_model             TEXT    NOT NULL DEFAULT '',
                acl_defaults_snapshot    TEXT    NOT NULL DEFAULT '{}',
                created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_used_at             DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            -- Per-profile persistent ACL grants and per-tool overrides (Issue #45,
            -- ADR-0005). once/session grants live in RAM (see security/acl.py).
            CREATE TABLE IF NOT EXISTS profile_acl (
                profile_id INTEGER NOT NULL,
                scope      TEXT    NOT NULL CHECK (scope IN ('class', 'tool')),
                target     TEXT    NOT NULL,
                policy     TEXT    NOT NULL CHECK (policy IN ('silent', 'ask', 'deny')),
                granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, scope, target),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
        """)
        self._con.commit()
        # Migrate pre-existing DBs that lack newer columns
        for col, default in [
            ("voice_sample", "''"),
            ("wake_sample",  "''"),
            ("active_model", "''"),
            ("acl_defaults_snapshot", "'{}'"),
        ]:
            try:
                self._con.execute(
                    f"ALTER TABLE profiles ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
                )
                self._con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        wake_name: str = "felix",
        pronunciation_guide: str = "",
        voice_id: str = "default",
        connected_accounts: list | None = None,
        voice_sample: str = "",
        wake_sample: str = "",
        acl_defaults_snapshot: dict | None = None,
    ) -> Profile:
        # Snapshot the current system default-default policy at creation
        # (Issue #45). Subsequent changes to DEFAULT_POLICY do not propagate.
        if acl_defaults_snapshot is None:
            from cerebral.security import DEFAULT_POLICY
            acl_defaults_snapshot = {
                cap.value: decision.value
                for cap, decision in DEFAULT_POLICY.items()
            }
        cur = self._con.execute(
            """INSERT INTO profiles
                   (name, wake_name, pronunciation_guide, voice_id,
                    connected_accounts, voice_sample, wake_sample,
                    acl_defaults_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, wake_name, pronunciation_guide, voice_id,
             json.dumps(connected_accounts or []), voice_sample, wake_sample,
             json.dumps(acl_defaults_snapshot)),
        )
        self._con.commit()
        return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def get(self, profile_id: int) -> Profile | None:
        row = self._con.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return _row_to_profile(row) if row else None

    def list_all(self) -> list[Profile]:
        rows = self._con.execute(
            "SELECT * FROM profiles ORDER BY last_used_at DESC"
        ).fetchall()
        return [_row_to_profile(r) for r in rows]

    def update(self, profile: Profile) -> Profile:
        self._con.execute(
            """UPDATE profiles
                  SET name=?, wake_name=?, pronunciation_guide=?,
                      voice_id=?, connected_accounts=?,
                      voice_sample=?, wake_sample=?,
                      last_used_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (profile.name, profile.wake_name, profile.pronunciation_guide,
             profile.voice_id, json.dumps(profile.connected_accounts),
             profile.voice_sample, profile.wake_sample, profile.id),
        )
        self._con.commit()
        return self.get(profile.id)  # type: ignore[arg-type]

    def update_voice(self, profile_id: int, voice_id: str) -> None:
        """Update only the voice_id for a profile — used by set_voice IPC message."""
        self._con.execute(
            "UPDATE profiles SET voice_id=? WHERE id=?", (voice_id, profile_id)
        )
        self._con.commit()

    def update_active_model(self, profile_id: int, model_id: str) -> None:
        """Persist the user's last-chosen ModelRouter model id (issue #37)."""
        self._con.execute(
            "UPDATE profiles SET active_model=? WHERE id=?", (model_id, profile_id)
        )
        self._con.commit()

    def delete(self, profile_id: int) -> None:
        self._con.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        if self._get_setting("active_profile_id") == str(profile_id):
            self._con.execute("DELETE FROM settings WHERE key='active_profile_id'")
        self._con.commit()

    # ── Per-profile ACL grants (Issue #45) ─────────────────────────────────────

    def set_acl_grant(
        self, profile_id: int, *, scope: str, target: str, policy: str,
    ) -> None:
        """Insert or update a persistent ACL grant for a profile.

        scope: "class" or "tool"
        target: capability class name (when scope="class") or tool name (when scope="tool")
        policy: "silent" | "ask" | "deny"

        Validated at the SQL CHECK level; callers should still pass canonical
        values from the capability vocabulary / decision enum.
        """
        if scope not in {"class", "tool"}:
            raise ValueError(f"scope must be 'class' or 'tool', got {scope!r}")
        if policy not in {"silent", "ask", "deny"}:
            raise ValueError(f"policy must be 'silent'/'ask'/'deny', got {policy!r}")
        self._con.execute(
            """INSERT INTO profile_acl (profile_id, scope, target, policy)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(profile_id, scope, target)
               DO UPDATE SET policy=excluded.policy,
                             granted_at=CURRENT_TIMESTAMP""",
            (profile_id, scope, target, policy),
        )
        self._con.commit()

    def revoke_acl_grant(
        self, profile_id: int, *, scope: str, target: str,
    ) -> bool:
        """Delete a persistent ACL grant. Returns True iff a row existed."""
        cur = self._con.execute(
            "DELETE FROM profile_acl WHERE profile_id=? AND scope=? AND target=?",
            (profile_id, scope, target),
        )
        self._con.commit()
        return cur.rowcount > 0

    def list_acl_grants(self, profile_id: int) -> list[dict]:
        """Return all persistent ACL rows for a profile, oldest first."""
        rows = self._con.execute(
            """SELECT scope, target, policy, granted_at
                 FROM profile_acl
                WHERE profile_id=?
                ORDER BY granted_at""",
            (profile_id,),
        ).fetchall()
        return [
            {"scope": r["scope"], "target": r["target"],
             "policy": r["policy"], "granted_at": r["granted_at"]}
            for r in rows
        ]

    def get_acl_grant(
        self, profile_id: int, *, scope: str, target: str,
    ) -> str | None:
        """Return the stored policy for (profile, scope, target), or None."""
        row = self._con.execute(
            """SELECT policy FROM profile_acl
                WHERE profile_id=? AND scope=? AND target=?""",
            (profile_id, scope, target),
        ).fetchone()
        return row["policy"] if row else None

    # ── Active profile ────────────────────────────────────────────────────────

    def get_active(self) -> Profile | None:
        pid = self._get_setting("active_profile_id")
        if pid:
            p = self.get(int(pid))
            if p:
                return p
        # Fall back to most-recently-used
        profiles = self.list_all()
        return profiles[0] if profiles else None

    def set_active(self, profile_id: int) -> None:
        self._set_setting("active_profile_id", str(profile_id))
        self._con.execute(
            "UPDATE profiles SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",
            (profile_id,),
        )
        self._con.commit()

    # ── Settings helpers ──────────────────────────────────────────────────────

    def _get_setting(self, key: str) -> str | None:
        row = self._con.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def _set_setting(self, key: str, value: str) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        self._con.commit()


def _row_to_profile(row: sqlite3.Row) -> Profile:
    keys = row.keys()
    snapshot_raw = row["acl_defaults_snapshot"] if "acl_defaults_snapshot" in keys else ""
    try:
        snapshot = json.loads(snapshot_raw) if snapshot_raw else {}
    except (ValueError, TypeError):
        snapshot = {}
    return Profile(
        id=row["id"],
        name=row["name"],
        wake_name=row["wake_name"],
        pronunciation_guide=row["pronunciation_guide"],
        voice_id=row["voice_id"],
        connected_accounts=json.loads(row["connected_accounts"]),
        voice_sample=row["voice_sample"] if "voice_sample" in keys else "",
        wake_sample =row["wake_sample"]  if "wake_sample"  in keys else "",
        active_model=row["active_model"] if "active_model" in keys else "",
        acl_defaults_snapshot=snapshot,
    )
