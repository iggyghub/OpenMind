"""Results store for CROSS-STOCK-VALIDATION S2 -- mirrors ReplayStore's
shape (cerebral/trading/replay_store.py), but a different table: one row
per (strategy, symbol) pair over the full 5-year window, not per
strategy-month."""
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from cerebral.paths import data_dir

DB_PATH = data_dir() / "cross_stock_results.db"


class CrossStockStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DB_PATH)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cross_stock_runs (
                run_id TEXT PRIMARY KEY,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cross_stock_results (
                run_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                net_return REAL,
                max_drawdown REAL,
                n_trades INTEGER,
                flat_reason TEXT,
                PRIMARY KEY (run_id, strategy_id, symbol)
            );
        """)
        self.conn.commit()

    def create_run(self, start: str, end: str) -> str:
        run_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO cross_stock_runs (run_id, start, end, created_at) VALUES (?, ?, ?, ?)",
            (run_id, start, end, created_at),
        )
        self.conn.commit()
        return run_id

    def record_result(
        self, run_id: str, strategy_id: str, symbol: str,
        net_return: Optional[float], max_drawdown: Optional[float],
        n_trades: int, flat_reason: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO cross_stock_results (
                run_id, strategy_id, symbol, net_return, max_drawdown, n_trades, flat_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, strategy_id, symbol) DO UPDATE SET
                net_return=excluded.net_return,
                max_drawdown=excluded.max_drawdown,
                n_trades=excluded.n_trades,
                flat_reason=excluded.flat_reason""",
            (run_id, strategy_id, symbol, net_return, max_drawdown, n_trades, flat_reason),
        )
        self.conn.commit()

    def get_results_by_strategy(self, strategy_id: str) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM cross_stock_results WHERE strategy_id = ?", (strategy_id,)
        )
        return cur.fetchall()

    def get_consistency_by_strategy(self) -> dict[str, float]:
        """Returns {strategy_id: fraction_of_positive_expectancy} for every
        strategy with at least one PAIR THAT ACTUALLY RAN. Omits a
        strategy entirely if it has zero successful pairs (whether that's
        zero attempts, or every attempt failing on missing bars/sandbox
        errors) -- missing data must never read as confirmed
        inconsistency, same convention worst_drawdown/cross_test_eligible
        already hold to.

        WHERE net_return IS NOT NULL matters, not just cosmetic: a failed
        pair (flat_reason set, net_return NULL) would otherwise count as
        0.0 in the CASE/AVG below -- a real bug caught before merging,
        since that conflates "couldn't test this stock" with "tested it
        and lost," silently dragging every strategy's score down by
        however many stocks happened to have bad data, for reasons having
        nothing to do with the strategy itself."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT strategy_id, AVG(CASE WHEN net_return > 0 THEN 1.0 ELSE 0.0 END) AS consistency
               FROM cross_stock_results
               WHERE net_return IS NOT NULL
               GROUP BY strategy_id"""
        )
        return {row["strategy_id"]: row["consistency"] for row in cur.fetchall()}
