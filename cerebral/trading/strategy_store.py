"""Persistent store for the strategy specs the live dispatcher needs.

A scheduler event only carries {title, start_iso, recurrence} -- enough to
say *when* a promoted strategy should be evaluated, nothing about *what* to
evaluate or *which symbol* to trade. This is the companion store that holds
that missing half, keyed by the same string the event uses as its title
(the strategy id).

Deliberately its own tiny table rather than extra columns on the scheduler
plugin's generic `events` table: `scheduler` is a calendar, not a trading
subsystem, and cerebral/ must not depend on plugins/ (seam rule #153/#385).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from cerebral.paths import data_dir

# Module-level Path (not str) so tests can monkeypatch it -- .parent.mkdir()
# below is pathlib-only, the S5c fixture bug.
_DB_PATH = data_dir() / "strategy_specs.db"


@dataclass(frozen=True)
class StrategySpec:
    """What the dispatcher needs to evaluate one promoted strategy.

    `code` is the source of a `def strategy(data) -> signals` function --
    see cerebral/trading/live_tick.py for the contract it must satisfy.
    Stored as source, not as a pickled callable: it has to survive a Felix
    restart, and it stays inspectable (a pickled callable is neither).
    """
    strategy_id: str
    symbol: str
    code: str
    qty: float = 1.0


class StrategyStore:
    def __init__(self, db_path=None) -> None:
        path = db_path if db_path is not None else _DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_specs (
                strategy_id TEXT PRIMARY KEY,
                symbol      TEXT NOT NULL,
                code        TEXT NOT NULL,
                qty         REAL NOT NULL DEFAULT 1.0,
                created_at  TEXT NOT NULL
            );
            """
        )
        self._con.commit()

    def save(self, spec: StrategySpec) -> None:
        """Register (or re-register, on re-validation) one strategy."""
        self._con.execute(
            "INSERT OR REPLACE INTO strategy_specs "
            "(strategy_id, symbol, code, qty, created_at) VALUES (?, ?, ?, ?, ?)",
            (spec.strategy_id, spec.symbol, spec.code, float(spec.qty),
             datetime.now(timezone.utc).isoformat()),
        )
        self._con.commit()

    def get(self, strategy_id: str) -> Optional[StrategySpec]:
        row = self._con.execute(
            "SELECT * FROM strategy_specs WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()
        if row is None:
            return None
        return StrategySpec(
            strategy_id=row["strategy_id"], symbol=row["symbol"],
            code=row["code"], qty=row["qty"],
        )

    def list_all(self) -> List[StrategySpec]:
        rows = self._con.execute(
            "SELECT * FROM strategy_specs ORDER BY created_at"
        ).fetchall()
        return [
            StrategySpec(strategy_id=r["strategy_id"], symbol=r["symbol"],
                         code=r["code"], qty=r["qty"])
            for r in rows
        ]

    def close(self) -> None:
        self._con.close()
