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
from typing import Iterable

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
    # Per-profile one-way unlock for the shell_exec capability (Issue #53,
    # ADR-0005). Defaults to False; the Permissions UI surfaces a one-time
    # confirmation modal that flips it to True and persists. Once unlocked,
    # the capability obeys whatever the user sets the class policy to.
    shell_exec_unlocked: bool = False
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
                shell_exec_unlocked      INTEGER NOT NULL DEFAULT 0 CHECK (shell_exec_unlocked IN (0, 1)),
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
            -- Per-plugin install metadata (Issue #51, ADR-0005). The
            -- builder sets new_plugin=1 on install; the Permissions UI
            -- clears it. While set, ProfileACL.resolve skips session/
            -- persistent/per-tool bypasses on this plugin's tools.
            -- Hand-authored plugins have no row here — get_plugin_new_flag
            -- returns False by default.
            CREATE TABLE IF NOT EXISTS plugin_flags (
                plugin_name   TEXT    PRIMARY KEY,
                new_plugin    INTEGER NOT NULL DEFAULT 0 CHECK (new_plugin IN (0, 1)),
                installed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            -- Per-profile Discord auto-reply allowlist (Issue #177, ADR-0006).
            -- Empty allowlist = drop every inbound DM (conservative default).
            -- A row routes inbound DMs from sender_id through the LLM pipeline
            -- and emits a real reply via discord_send_message, subject to the
            -- detection-mitigation defaults in cerebral/discord_auto_reply.py.
            CREATE TABLE IF NOT EXISTS discord_user_allowlist (
                profile_id INTEGER NOT NULL,
                sender_id  TEXT    NOT NULL,
                note       TEXT    NOT NULL DEFAULT '',
                added_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, sender_id),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            -- Per-profile slice-2 detection-mitigation settings (Issue #177).
            -- Free-form key/value strings so adding a new knob is a code
            -- change, not a schema migration. Defaults live in
            -- cerebral/discord_auto_reply.py; this table holds only the
            -- overrides. ``key`` namespace is intentionally flat.
            CREATE TABLE IF NOT EXISTS discord_user_settings (
                profile_id INTEGER NOT NULL,
                key        TEXT    NOT NULL,
                value      TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (profile_id, key),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
        """)
        self._con.commit()
        # Migrate pre-existing DBs that lack newer columns
        for col, col_type, default in [
            ("voice_sample", "TEXT", "''"),
            ("wake_sample",  "TEXT", "''"),
            ("active_model", "TEXT", "''"),
            ("acl_defaults_snapshot", "TEXT", "'{}'"),
            ("shell_exec_unlocked", "INTEGER", "0"),
        ]:
            try:
                self._con.execute(
                    f"ALTER TABLE profiles ADD COLUMN {col} {col_type} NOT NULL DEFAULT {default}"
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

    def unlock_shell_exec(self, profile_id: int) -> None:
        """One-way flip of the per-profile shell_exec lock (Issue #53).

        ``shell_exec`` defaults to ``deny`` in the day-1 ACL because it is
        the highest-blast-radius capability. Power users explicitly opt in
        via the Permissions UI's one-time confirmation modal; this setter
        records the opt-in. Reverting to locked is intentionally not a
        single click — clear the row by deleting and recreating the profile.
        """
        self._con.execute(
            "UPDATE profiles SET shell_exec_unlocked=1 WHERE id=?", (profile_id,)
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

    # ── Plugin flags + uninstall ACL cleanup (Issue #51) ──────────────────────

    def set_plugin_new_flag(self, plugin_name: str, value: bool) -> None:
        """Set the 'new plugin' flag for a builder-installed plugin.

        While True, ``ProfileACL.resolve`` short-circuits the plugin's tools
        to step 5 (default policy) so session/persistent bypasses cannot
        silently fire. The Permissions UI flips it to False once the user
        has reviewed the plugin (issue #53)."""
        self._con.execute(
            """INSERT INTO plugin_flags (plugin_name, new_plugin)
               VALUES (?, ?)
               ON CONFLICT(plugin_name)
               DO UPDATE SET new_plugin=excluded.new_plugin""",
            (plugin_name, 1 if value else 0),
        )
        self._con.commit()

    def get_plugin_new_flag(self, plugin_name: str) -> bool:
        """Return the 'new plugin' flag for a plugin (False if no row)."""
        row = self._con.execute(
            "SELECT new_plugin FROM plugin_flags WHERE plugin_name=?",
            (plugin_name,),
        ).fetchone()
        return bool(row["new_plugin"]) if row else False

    def remove_plugin_flag(self, plugin_name: str) -> bool:
        """Drop the plugin_flags row for a plugin. Returns True iff a row existed."""
        cur = self._con.execute(
            "DELETE FROM plugin_flags WHERE plugin_name=?", (plugin_name,),
        )
        self._con.commit()
        return cur.rowcount > 0

    def list_plugin_flags(self) -> dict[str, bool]:
        """Map of plugin_name → new_plugin flag for every row present.

        Plugins with no row default to False at lookup time — they don't
        appear here. The tray's plugins_list uses this to render the
        "new plugin" badge (Issue #51 / Issue #53)."""
        rows = self._con.execute(
            "SELECT plugin_name, new_plugin FROM plugin_flags"
        ).fetchall()
        return {r["plugin_name"]: bool(r["new_plugin"]) for r in rows}

    def remove_plugin_acl_rows(
        self, plugin_name: str, tool_names: Iterable[str],
    ) -> int:
        """Drop per-tool ``profile_acl`` rows for a plugin's tools, across all profiles.

        v1 (per #51 sharpener pin #5): only per-tool overrides are dropped.
        Class-scope rows (``scope='class'``) survive — a user who granted
        a capability persistently to one plugin probably wants it for the
        next one too. Returns the number of rows deleted.

        ``plugin_name`` is currently informational (logging only) — we
        match per-tool overrides by the explicit ``tool_names`` list,
        which the orchestrator computes from the plugin before unregister.
        """
        tools = [name for name in tool_names if name]
        if not tools:
            return 0
        placeholders = ",".join("?" * len(tools))
        cur = self._con.execute(
            f"DELETE FROM profile_acl WHERE scope='tool' AND target IN ({placeholders})",
            tools,
        )
        self._con.commit()
        return cur.rowcount

    # ── Discord auto-reply allowlist + settings (Issue #177) ─────────────────

    def add_discord_allowlist(
        self, profile_id: int, sender_id: str, note: str = "",
    ) -> None:
        """Insert or refresh a row in the Discord auto-reply allowlist."""
        if not sender_id:
            raise ValueError("sender_id must be non-empty")
        self._con.execute(
            """INSERT INTO discord_user_allowlist (profile_id, sender_id, note)
               VALUES (?, ?, ?)
               ON CONFLICT(profile_id, sender_id)
               DO UPDATE SET note=excluded.note,
                             added_at=CURRENT_TIMESTAMP""",
            (profile_id, sender_id, note or ""),
        )
        self._con.commit()

    def remove_discord_allowlist(
        self, profile_id: int, sender_id: str,
    ) -> bool:
        """Drop a row. Returns True iff a row existed."""
        cur = self._con.execute(
            "DELETE FROM discord_user_allowlist WHERE profile_id=? AND sender_id=?",
            (profile_id, sender_id),
        )
        self._con.commit()
        return cur.rowcount > 0

    def list_discord_allowlist(self, profile_id: int) -> list[dict]:
        rows = self._con.execute(
            """SELECT sender_id, note, added_at
                 FROM discord_user_allowlist
                WHERE profile_id=?
                ORDER BY added_at""",
            (profile_id,),
        ).fetchall()
        return [
            {"sender_id": r["sender_id"], "note": r["note"],
             "added_at": r["added_at"]}
            for r in rows
        ]

    def is_discord_allowlisted(
        self, profile_id: int, sender_id: str,
    ) -> bool:
        if not sender_id:
            return False
        row = self._con.execute(
            "SELECT 1 FROM discord_user_allowlist WHERE profile_id=? AND sender_id=?",
            (profile_id, sender_id),
        ).fetchone()
        return row is not None

    def set_discord_setting(
        self, profile_id: int, key: str, value: str,
    ) -> None:
        if not key:
            raise ValueError("key must be non-empty")
        self._con.execute(
            """INSERT INTO discord_user_settings (profile_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(profile_id, key)
               DO UPDATE SET value=excluded.value""",
            (profile_id, key, value),
        )
        self._con.commit()

    def get_discord_setting(
        self, profile_id: int, key: str,
    ) -> str | None:
        row = self._con.execute(
            "SELECT value FROM discord_user_settings WHERE profile_id=? AND key=?",
            (profile_id, key),
        ).fetchone()
        return row["value"] if row else None

    def list_discord_settings(self, profile_id: int) -> dict[str, str]:
        rows = self._con.execute(
            "SELECT key, value FROM discord_user_settings WHERE profile_id=?",
            (profile_id,),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def clear_discord_setting(
        self, profile_id: int, key: str,
    ) -> bool:
        cur = self._con.execute(
            "DELETE FROM discord_user_settings WHERE profile_id=? AND key=?",
            (profile_id, key),
        )
        self._con.commit()
        return cur.rowcount > 0

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
        shell_exec_unlocked=bool(row["shell_exec_unlocked"]) if "shell_exec_unlocked" in keys else False,
    )
