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
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    NOT NULL,
                wake_name           TEXT    NOT NULL DEFAULT 'felix',
                pronunciation_guide TEXT    NOT NULL DEFAULT '',
                voice_id            TEXT    NOT NULL DEFAULT 'default',
                connected_accounts  TEXT    NOT NULL DEFAULT '[]',
                voice_sample        TEXT    NOT NULL DEFAULT '',
                wake_sample         TEXT    NOT NULL DEFAULT '',
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_used_at        DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self._con.commit()
        # Migrate pre-existing DBs that lack voice_sample
        for col, default in [("voice_sample", "''"), ("wake_sample", "''")]:
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
    ) -> Profile:
        cur = self._con.execute(
            """INSERT INTO profiles
                   (name, wake_name, pronunciation_guide, voice_id,
                    connected_accounts, voice_sample, wake_sample)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, wake_name, pronunciation_guide, voice_id,
             json.dumps(connected_accounts or []), voice_sample, wake_sample),
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

    def delete(self, profile_id: int) -> None:
        self._con.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        if self._get_setting("active_profile_id") == str(profile_id):
            self._con.execute("DELETE FROM settings WHERE key='active_profile_id'")
        self._con.commit()

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
    return Profile(
        id=row["id"],
        name=row["name"],
        wake_name=row["wake_name"],
        pronunciation_guide=row["pronunciation_guide"],
        voice_id=row["voice_id"],
        connected_accounts=json.loads(row["connected_accounts"]),
        voice_sample=row["voice_sample"] if "voice_sample" in row.keys() else "",
        wake_sample =row["wake_sample"]  if "wake_sample"  in row.keys() else "",
    )
