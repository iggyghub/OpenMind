"""
Module for tracking forward paper trading records.
Persists fills to SQLite, computes rolling CI on expectancy,
and enforces a 30-trade minimum for "meaningful" records.
"""
from __future__ import annotations

import sqlite3
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from cerebral.paths import data_dir

_DB_PATH = data_dir() / "forward_fills.db"

@dataclass
class ForwardRecord:
    """Tracks paper trading fills, computes rolling CI on expectancy, enforces 30-trade min."""
    _con: sqlite3.Connection = field(default=None, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)

    def __post_init__(self):
        if not self._initialized:
            path = _DB_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(str(path), check_same_thread=False)
            self._con.row_factory = sqlite3.Row
            self._con.executescript("""
                CREATE TABLE IF NOT EXISTS forward_fills (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    phase       TEXT    NOT NULL DEFAULT 'paper',
                    symbol      TEXT    NOT NULL,
                    side        TEXT    NOT NULL,
                    qty         REAL    NOT NULL,
                    price       REAL    NOT NULL,
                    fees        REAL    NOT NULL DEFAULT 0.0,
                    pnl         REAL    NOT NULL DEFAULT 0.0,
                    strategy_id TEXT    NOT NULL DEFAULT 'global'
                );
            """)
            self._con.commit()
            self._initialized = True

    def add_fill(self, symbol: str, side: str, qty: float, price: float, fees: float = 0.0, pnl: float = 0.0, phase: str = "paper", strategy_id: str = "global") -> None:
        """Persist a new broker fill."""
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute(
            "INSERT INTO forward_fills (timestamp, phase, symbol, side, qty, price, fees, pnl, strategy_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, phase, symbol, side, qty, price, fees, pnl, strategy_id),
        )
        self._con.commit()

    def add_live_fill(self, symbol: str, side: str, qty: float, price: float, fees: float = 0.0, pnl: float = 0.0, strategy_id: str = "global") -> None:
        """Persist a live trading fill."""
        self.add_fill(symbol, side, qty, price, fees, pnl, phase="live", strategy_id=strategy_id)

    def get_daily_pnl(self, day_iso: Optional[str] = None) -> float:
        """Total realized P&L across all strategies for one UTC calendar day
        (defaults to today) -- feeds RiskManager's account-wide daily-loss
        halt (S20/#873). Not scoped to one strategy_id: the halt is meant to
        stop all trading for the day, not just one strategy's."""
        day = day_iso or datetime.now(timezone.utc).date().isoformat()
        row = self._con.execute(
            "SELECT COALESCE(SUM(pnl), 0.0) FROM forward_fills WHERE substr(timestamp, 1, 10) = ?",
            (day,),
        ).fetchone()
        return float(row[0])

    def get_live_fill_count(self, strategy_id: str = "global") -> int:
        return self._con.execute("SELECT COUNT(*) FROM forward_fills WHERE phase = 'live' AND strategy_id = ?", (strategy_id,)).fetchone()[0]

    def get_live_pnls(self, strategy_id: str = "global") -> list[float]:
        rows = self._con.execute("SELECT pnl FROM forward_fills WHERE phase = 'live' AND strategy_id = ? ORDER BY timestamp ASC", (strategy_id,)).fetchall()
        return [r["pnl"] for r in rows]

    def compute_live_expectancy_ci(self, strategy_id: str = "global") -> tuple[float, float, float, bool]:
        """Same as compute_expectancy_ci but restricted to live trades."""
        pnls = self.get_live_pnls(strategy_id)
        n = len(pnls)
        if n == 0:
            return 0.0, 0.0, 0.0, False
        
        mean = float(np.mean(pnls))
        se = float(np.std(pnls, ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        lower = mean - 1.96 * se
        upper = mean + 1.96 * se
        is_sufficient = n >= 30
        return mean, lower, upper, is_sufficient

    def get_fills(self, limit: Optional[int] = None, strategy_id: str = "global") -> List[sqlite3.Row]:
        query = "SELECT * FROM forward_fills WHERE strategy_id = ? ORDER BY timestamp DESC"
        if limit:
            query += f" LIMIT {limit}"
        return self._con.execute(query, (strategy_id,)).fetchall()

    def trade_count(self, strategy_id: str = "global") -> int:
        return self._con.execute("SELECT COUNT(*) FROM forward_fills WHERE strategy_id = ?", (strategy_id,)).fetchone()[0]

    def compute_expectancy_ci(self, strategy_id: str = "global") -> Tuple[float, float, float, bool]:
        """Returns (mean, lower_ci, upper_ci, is_sufficient) for realized PnL.
        Uses mean +/- 1.96 * SE. Marks insufficient if < 30 trades.
        """
        n = self.trade_count(strategy_id)
        if n == 0:
            return 0.0, 0.0, 0.0, False
        
        rows = self._con.execute("SELECT pnl FROM forward_fills WHERE strategy_id = ?", (strategy_id,)).fetchall()
        pnls = np.array([r["pnl"] for r in rows])
        
        mean = float(np.mean(pnls))
        se = float(np.std(pnls, ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        lower = mean - 1.96 * se
        upper = mean + 1.96 * se
        
        is_sufficient = n >= 30
        return mean, lower, upper, is_sufficient

    def get_equity_curve(self, strategy_id: str = "global") -> List[float]:
        """Returns cumulative PnL equity curve chronologically."""
        rows = self._con.execute("SELECT pnl FROM forward_fills WHERE strategy_id = ? ORDER BY timestamp ASC", (strategy_id,)).fetchall()
        pnls = [r["pnl"] for r in rows]
        equity = []
        cum = 0.0
        for p in pnls:
            cum += p
            equity.append(cum)
        return equity

    def close(self) -> None:
        if self._con:
            self._con.close()
