"""Persistent step ledger -- crash-resumable tool chains (harness improvement C).

Generalizes the video store's per-item resume pattern (ADR-0017 decision 4) to
arbitrary tool chains: each completed step is committed to the DB keyed by
(run_id, step signature), so a chain re-invoked after a Cerebral crash replays
its finished steps from the ledger instead of re-executing side-effectful tools
(no double-sent email, no re-submitted form, no re-purchase).

Backed by the shared openmind.db with the same connect/commit idiom as
VideoStore. Wired into ChainEngine as an opt-in (run_id + ledger); when either
is absent the chain runs exactly as before with no persistence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cerebral.paths import data_dir

_DEFAULT_DB = data_dir() / "openmind.db"

_DDL = """
CREATE TABLE IF NOT EXISTS chain_steps (
    run_id      TEXT    NOT NULL,
    step_sig    TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    args_json   TEXT    NOT NULL,
    result_json TEXT,
    is_error    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (run_id, step_sig)
);
"""


class StepLedger:
    """SQLite-backed record of completed chain steps, keyed by run_id."""

    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_DDL)

    def record(self, run_id: str, sig: str, step: dict) -> None:
        """Persist one completed step. step is the ChainEngine step dict
        {"name", "args", "result", "is_error"}. seq is the next position in this
        run's ledger, preserving execution order for replay.

        ponytail: INSERT OR REPLACE assumes each sig is recorded once per run
        (ChainEngine adds the sig to completed_sigs and never re-executes it). A
        re-record would reorder the row to the end -- fine, that path never fires.
        """
        con = self._con
        seq = con.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM chain_steps WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        con.execute(
            "INSERT OR REPLACE INTO chain_steps "
            "(run_id, step_sig, seq, name, args_json, result_json, is_error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                sig,
                seq,
                step["name"],
                json.dumps(step.get("args", {}), default=str),
                json.dumps(step.get("result"), default=str),
                int(bool(step.get("is_error"))),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()

    def completed(self, run_id: str) -> list[dict]:
        """Completed steps for a run in execution order (empty if none). Each
        dict: {"sig", "name", "args", "result", "is_error"}."""
        rows = self._con.execute(
            "SELECT step_sig, name, args_json, result_json, is_error "
            "FROM chain_steps WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [
            {
                "sig": r["step_sig"],
                "name": r["name"],
                "args": json.loads(r["args_json"]),
                "result": (
                    json.loads(r["result_json"])
                    if r["result_json"] is not None
                    else None
                ),
                "is_error": bool(r["is_error"]),
            }
            for r in rows
        ]

    def clear(self, run_id: str) -> int:
        """Delete a run's ledger once the chain finishes cleanly. Returns the
        number of step rows removed."""
        cur = self._con.execute("DELETE FROM chain_steps WHERE run_id = ?", (run_id,))
        self._con.commit()
        return cur.rowcount
