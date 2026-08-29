"""
Module for tracking forward paper trading records.
Persists fills to SQLite, computes rolling CI on expectancy,
and enforces a 30-trade minimum for "meaningful" records.
"""
from __future__ import annotations

import json
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
                CREATE TABLE IF NOT EXISTS paper_archives (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    reset_at          TEXT    NOT NULL,
                    fills_json        TEXT    NOT NULL,
                    total_pnl         REAL    NOT NULL,
                    trade_count       INTEGER NOT NULL,
                    date_range_start  TEXT,
                    date_range_end    TEXT
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

    def get_total_pnl(self) -> float:
        """Total realized P&L across every strategy, all-time -- the grand
        total for the (hand-built, tray/lib/trading-panel.js) Overview
        tab's multi-strategy graph. Same query shape as get_daily_pnl
        above, minus the date filter."""
        row = self._con.execute(
            "SELECT COALESCE(SUM(pnl), 0.0) FROM forward_fills",
        ).fetchone()
        return float(row[0])

    def reset_paper(self) -> dict:
        """Archives every current PAPER-phase fill as one self-contained
        historical block (for the Overview tab's collapsible history
        section), then clears them from the live table. Existing read
        methods (get_total_pnl, get_equity_curve, get_all_fills, etc.)
        need NO changes -- they just see a smaller live table afterward.
        Leaves any 'live' fills alone -- this is a paper-trading reset,
        not a full wipe. Returns {"archived": bool, "fills": int,
        "total_pnl": float} -- archived is False (no-op, nothing to
        archive) if there were zero paper fills."""
        rows = self._con.execute(
            "SELECT * FROM forward_fills WHERE phase = 'paper' ORDER BY timestamp ASC"
        ).fetchall()
        if not rows:
            return {"archived": False, "fills": 0, "total_pnl": 0.0}
        fills = [dict(r) for r in rows]
        total_pnl = sum(f["pnl"] for f in fills)
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute(
            "INSERT INTO paper_archives "
            "(reset_at, fills_json, total_pnl, trade_count, date_range_start, date_range_end) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, json.dumps(fills), total_pnl, len(fills),
             fills[0]["timestamp"], fills[-1]["timestamp"]),
        )
        self._con.execute("DELETE FROM forward_fills WHERE phase = 'paper'")
        self._con.commit()
        return {"archived": True, "fills": len(fills), "total_pnl": total_pnl}

    def list_paper_archives(self) -> List[dict]:
        """Summary of every past reset 'block', newest first -- for the
        Overview tab's collapsible history section. Does NOT include the
        fills themselves (fills_json) -- see get_paper_archive_fills for
        that, kept separate so the summary list stays cheap to broadcast."""
        rows = self._con.execute(
            "SELECT id, reset_at, total_pnl, trade_count, date_range_start, date_range_end "
            "FROM paper_archives ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_paper_archive_fills(self, archive_id: int) -> List[dict]:
        """The actual fills inside one archived block, if the user expands
        it on the Overview tab."""
        row = self._con.execute(
            "SELECT fills_json FROM paper_archives WHERE id = ?", (archive_id,)
        ).fetchone()
        return json.loads(row["fills_json"]) if row else []

    def get_live_fill_count(self, strategy_id: str = "global") -> int:
        return self._con.execute("SELECT COUNT(*) FROM forward_fills WHERE phase = 'live' AND strategy_id = ?", (strategy_id,)).fetchone()[0]

    def get_live_pnls(self, strategy_id: str = "global") -> list[float]:
        rows = self._con.execute("SELECT pnl FROM forward_fills WHERE phase = 'live' AND strategy_id = ? ORDER BY timestamp ASC", (strategy_id,)).fetchall()
        return [r["pnl"] for r in rows]

    def get_paper_pnls(self, strategy_id: str = "global") -> list[float]:
        rows = self._con.execute("SELECT pnl FROM forward_fills WHERE phase = 'paper' AND strategy_id = ? ORDER BY timestamp ASC", (strategy_id,)).fetchall()
        return [r["pnl"] for r in rows]

    def compute_live_expectancy_ci(
        self, strategy_id: str = "global", floor: Optional[int] = None,
    ) -> tuple[float, float, float, bool, int, int]:
        """Same as compute_expectancy_ci but restricted to live trades."""
        pnls = self.get_live_pnls(strategy_id)
        n = len(pnls)
        if n == 0:
            return 0.0, 0.0, 0.0, False, 0, 0

        mean = float(np.mean(pnls))
        se = float(np.std(pnls, ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        lower = mean - 1.96 * se
        upper = mean + 1.96 * se

        distinct_days = self.get_distinct_days(strategy_id)
        if floor is None:
            floor = self._distinct_days_floor()

        is_sufficient = n >= 30 and distinct_days >= floor
        return mean, lower, upper, is_sufficient, n, distinct_days

    def compute_paper_expectancy_ci(
        self, strategy_id: str = "global", floor: Optional[int] = None,
    ) -> tuple[float, float, float, bool, int, int]:
        """Same as compute_expectancy_ci but restricted to paper trades."""
        pnls = self.get_paper_pnls(strategy_id)
        n = len(pnls)
        if n == 0:
            return 0.0, 0.0, 0.0, False, 0, 0

        mean = float(np.mean(pnls))
        se = float(np.std(pnls, ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        lower = mean - 1.96 * se
        upper = mean + 1.96 * se

        distinct_days = self.get_distinct_days(strategy_id)
        if floor is None:
            floor = self._distinct_days_floor()

        is_sufficient = n >= 30 and distinct_days >= floor
        return mean, lower, upper, is_sufficient, n, distinct_days

    @staticmethod
    def _distinct_days_floor() -> int:
        """Default distinct-days floor, read from the real SettingsStore.

        Only called when a caller doesn't pass floor= explicitly -- tests
        and other callers that want an isolated/deterministic value should
        pass floor= directly rather than relying on (and touching) the
        real production settings file.
        """
        try:
            from cerebral.settings import SettingsStore
            return SettingsStore().get("distinct_days_floor") or 30
        except Exception:
            return 30

    def get_distinct_days(self, strategy_id: str = "global") -> int:
        """Count distinct trading days (UTC calendar dates) for a strategy."""
        row = self._con.execute(
            "SELECT COUNT(DISTINCT substr(timestamp, 1, 10)) FROM forward_fills WHERE strategy_id = ?",
            (strategy_id,)
        ).fetchone()
        return int(row[0])

    def get_fills(self, limit: Optional[int] = None, strategy_id: str = "global") -> List[sqlite3.Row]:
        query = "SELECT * FROM forward_fills WHERE strategy_id = ? ORDER BY timestamp DESC"
        if limit:
            query += f" LIMIT {limit}"
        return self._con.execute(query, (strategy_id,)).fetchall()

    def get_all_fills(self, limit: Optional[int] = None) -> List[sqlite3.Row]:
        """Every fill across every strategy, chronological (oldest first --
        unlike get_fills' newest-first "recent activity" ordering, this
        feeds a per-symbol running/cumulative chart, which needs oldest-
        first to build correctly). No strategy_id filter at all -- for the
        (hand-built, tray/lib/trading-panel.js) Overview tab's by-symbol
        chart, which groups these client-side by `symbol`."""
        query = "SELECT * FROM forward_fills ORDER BY timestamp ASC"
        if limit:
            query += f" LIMIT {limit}"
        return self._con.execute(query).fetchall()

    def trade_count(self, strategy_id: str = "global") -> int:
        return self._con.execute("SELECT COUNT(*) FROM forward_fills WHERE strategy_id = ?", (strategy_id,)).fetchone()[0]

    def compute_expectancy_ci(
        self, strategy_id: str = "global", floor: Optional[int] = None,
    ) -> Tuple[float, float, float, bool, int, int]:
        """Returns (mean, lower_ci, upper_ci, is_sufficient, trade_count, distinct_days) for realized PnL.
        Uses mean +/- 1.96 * SE. Marks insufficient if < 30 trades OR < configured distinct trading days.
        """
        n = self.trade_count(strategy_id)
        distinct_days = self.get_distinct_days(strategy_id)
        if n == 0:
            return 0.0, 0.0, 0.0, False, 0, 0

        rows = self._con.execute("SELECT pnl FROM forward_fills WHERE strategy_id = ?", (strategy_id,)).fetchall()
        pnls = np.array([r["pnl"] for r in rows])

        mean = float(np.mean(pnls))
        se = float(np.std(pnls, ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        lower = mean - 1.96 * se
        upper = mean + 1.96 * se

        if floor is None:
            floor = self._distinct_days_floor()

        is_sufficient = n >= 30 and distinct_days >= floor
        return mean, lower, upper, is_sufficient, n, distinct_days

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

    def compute_confidence_weight(self, strategy_id: str = "global") -> float:
        """Computes a continuous confidence weight for a strategy based on paper + live fills.

        Reuses `compute_live_expectancy_ci` for the live half. `compute_expectancy_ci`
        is NOT reused for the paper half -- it queries every fill regardless of
        `phase` (see its own docstring/implementation), so its mean is the blended
        paper+live mean, not a paper-only one. Using it here would double-count
        live fills' influence and mislabel the blend as "paper". Paper-only stats
        are computed directly, phase-filtered, the same way compute_live_expectancy_ci
        already does for the live side.

        Live results are weighted higher than paper results.
        Trade count scales the weight continuously toward the raw expectancy mean,
        never snapping to a hard 0 or 1 gate.
        Returns a float weight.
        """
        live_mean, _, _, _, live_n, _ = self.compute_live_expectancy_ci(strategy_id)
        paper_mean, _, _, _, paper_n, _ = self.compute_paper_expectancy_ci(strategy_id)

        if paper_n == 0 and live_n == 0:
            return 0.0

        live_weight = 2.0  # Live fills count double for confidence purposes
        denom = paper_n + live_n * live_weight

        weighted_mean = (paper_n * paper_mean + live_n * live_mean * live_weight) / denom
        # Continuous scaling: magnitude approaches raw expectancy as count hits the 30-trade floor
        scale = max(1e-4, min(1.0, (paper_n + live_n) / 30.0))
        return weighted_mean * scale
