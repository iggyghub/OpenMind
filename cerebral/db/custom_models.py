"""
Per-profile custom (remote) model registry.

Stores the non-secret config for a user-added remote model server so it can be
re-registered into the ModelRouter at startup. The api_key itself never lands
here — it lives in the OS keyring via CredentialStore (provider=secret_ref,
field="api_token"); this table only records the secret_ref pointer.

  id         "custom/<slug>" — the ModelRouter backend id
  kind       ollama | openai | anthropic (see router.CUSTOM_KINDS)
  url        remote host (empty for anthropic)
  model      model name passed to the backend (empty '' for dynamic until first resolve)
  label      display label in the picker
  is_cloud   1 when hidden under the local-only kill-switch
  secret_ref keyring provider namespace for the api_key, or '' when none
  dynamic    1 = server-first: model auto-resolved from the server; `model`
             then doubles as the last-resolved cache for display + fast path
  context_window  optional per-model token window (issue #760); 0 = unset,
             ModelRouter.context_window_for() falls back to its
             _DEFAULT_CONTEXT_WINDOW floor (8192) when unset
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cerebral.paths import data_dir

DB_PATH = data_dir() / "openmind.db"


class CustomModelStore:
    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS custom_models (
                profile_id INTEGER NOT NULL,
                id         TEXT    NOT NULL,
                kind       TEXT    NOT NULL,
                url        TEXT    NOT NULL DEFAULT '',
                model      TEXT    NOT NULL,
                label      TEXT    NOT NULL DEFAULT '',
                is_cloud   INTEGER NOT NULL DEFAULT 0 CHECK (is_cloud IN (0, 1)),
                secret_ref TEXT    NOT NULL DEFAULT '',
                dynamic    INTEGER NOT NULL DEFAULT 0 CHECK (dynamic IN (0, 1)),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, id),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
        """)
        # ponytail: S1 (#528) shipped without `dynamic`; a dev box with the
        # older table needs the column added. Skips cleanly on fresh tables.
        for _col in (
            "dynamic INTEGER NOT NULL DEFAULT 0",
            "supports_vision INTEGER NOT NULL DEFAULT 0",
            "context_window INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                self._con.execute(f"ALTER TABLE custom_models ADD COLUMN {_col}")
            except sqlite3.OperationalError:
                pass
        self._con.commit()

    def add(
        self, profile_id: int, *, id: str, kind: str, url: str, model: str,
        label: str, is_cloud: bool, secret_ref: str = "", dynamic: bool = False,
        supports_vision: bool = False, context_window: int = 0,
    ) -> None:
        """Upsert a custom model row for (profile, id)."""
        self._con.execute(
            """INSERT INTO custom_models
                   (profile_id, id, kind, url, model, label, is_cloud,
                    secret_ref, dynamic, supports_vision, context_window)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, id)
               DO UPDATE SET kind=excluded.kind, url=excluded.url,
                             model=excluded.model, label=excluded.label,
                             is_cloud=excluded.is_cloud,
                             secret_ref=excluded.secret_ref,
                             dynamic=excluded.dynamic,
                             supports_vision=excluded.supports_vision,
                             context_window=excluded.context_window""",
            (profile_id, id, kind, url, model, label,
             1 if is_cloud else 0, secret_ref, 1 if dynamic else 0,
             1 if supports_vision else 0, int(context_window or 0)),
        )
        self._con.commit()

    def remove(self, profile_id: int, id: str) -> bool:
        """Drop a row. Returns True iff a row existed."""
        cur = self._con.execute(
            "DELETE FROM custom_models WHERE profile_id=? AND id=?", (profile_id, id)
        )
        self._con.commit()
        return cur.rowcount > 0

    def list(self, profile_id: int) -> list[dict]:
        rows = self._con.execute(
            """SELECT id, kind, url, model, label, is_cloud, secret_ref, dynamic,
                      supports_vision, context_window
                 FROM custom_models WHERE profile_id=? ORDER BY created_at""",
            (profile_id,),
        ).fetchall()
        return [
            {"id": r["id"], "kind": r["kind"], "url": r["url"], "model": r["model"],
             "label": r["label"], "is_cloud": bool(r["is_cloud"]),
             "secret_ref": r["secret_ref"], "dynamic": bool(r["dynamic"]),
             "supports_vision": bool(r["supports_vision"]),
             "context_window": r["context_window"]}
            for r in rows
        ]
