"""Results store for CROSS-STOCK-VALIDATION S2 -- mirrors ReplayStore's
shape (cerebral/trading/replay_store.py), but a different table: one row
per (strategy, symbol) pair over the full 5-year window, not per
strategy-month.

F1 (#1246): primary key changed from (run_id, strategy_id, symbol) to
(strategy_id, symbol) -- run_id is now provenance-only.  This makes the
ON CONFLICT upsert actually fire on re-runs, and lets resume logic use
"which pairs are already in the table" as the progress source of truth
instead of a settings cursor.  created_at lets future refresh logic
select stale pairs by age (no automatic cadence added here; see ADR-0028
rule 2)."""
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

        # F1 (#1246): detect old schema (PK includes run_id) and migrate.
        # As of 2026-09-15 the table holds exactly 22 leftover S2 verification
        # rows (confirmed via direct DB query before this branch was cut) --
        # dropping and recreating is safe and simpler than ALTER TABLE (SQLite
        # cannot change a PK constraint in-place).
        cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cross_stock_results'"
        )
        row = cur.fetchone()
        if row and "PRIMARY KEY (run_id" in row[0]:
            cur.execute("SELECT COUNT(*) FROM cross_stock_results")
            count = cur.fetchone()[0]
            if count > 100:
                raise RuntimeError(
                    f"cross_stock_results has {count} rows -- write a migration "
                    "instead of dropping (F1 expected <= 100 verification rows)"
                )
            cur.execute("DROP TABLE cross_stock_results")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cross_stock_results (
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                run_id TEXT,
                net_return REAL,
                max_drawdown REAL,
                n_trades INTEGER,
                flat_reason TEXT,
                benchmark_return REAL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (strategy_id, symbol)
            );
        """)
        # F3 (#1248): add benchmark_return to existing tables that predate this column.
        cur.execute("PRAGMA table_info(cross_stock_results)")
        existing_cols = {row[1] for row in cur.fetchall()}
        if "benchmark_return" not in existing_cols:
            cur.execute("ALTER TABLE cross_stock_results ADD COLUMN benchmark_return REAL")
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
        benchmark_return: Optional[float] = None,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO cross_stock_results (
                strategy_id, symbol, run_id, net_return, max_drawdown, n_trades, flat_reason, benchmark_return, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id, symbol) DO UPDATE SET
                run_id=excluded.run_id,
                net_return=excluded.net_return,
                max_drawdown=excluded.max_drawdown,
                n_trades=excluded.n_trades,
                flat_reason=excluded.flat_reason,
                benchmark_return=excluded.benchmark_return,
                created_at=excluded.created_at""",
            (strategy_id, symbol, run_id, net_return, max_drawdown, n_trades, flat_reason, benchmark_return, created_at),
        )
        self.conn.commit()

    def get_results_by_strategy(self, strategy_id: str) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM cross_stock_results WHERE strategy_id = ?", (strategy_id,)
        )
        return cur.fetchall()

    def get_done_pairs(self) -> set:
        """Returns the set of (strategy_id, symbol) tuples already recorded.
        Used by _run_cross_stock_replay to skip pairs on resume."""
        cur = self.conn.cursor()
        cur.execute("SELECT strategy_id, symbol FROM cross_stock_results")
        return {(row["strategy_id"], row["symbol"]) for row in cur.fetchall()}

    def get_done_count(self) -> int:
        """Total rows in the results table (includes failed pairs)."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cross_stock_results")
        return cur.fetchone()[0]

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

    def get_mean_excess_return_by_strategy(self) -> dict[str, Optional[float]]:
        """Returns {strategy_id: mean(net_return - benchmark_return)} for pairs
        where both values are non-NULL.  A strategy with no qualifying pairs is
        absent from the dict (same missing-data convention as the other rollups).
        Used to surface excess-over-benchmark alongside consistency in status."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT strategy_id, AVG(net_return - benchmark_return) AS mean_excess
               FROM cross_stock_results
               WHERE net_return IS NOT NULL AND benchmark_return IS NOT NULL
               GROUP BY strategy_id"""
        )
        return {row["strategy_id"]: row["mean_excess"] for row in cur.fetchall()}

    def get_tested_count_by_strategy(self) -> dict[str, int]:
        """Returns {strategy_id: count of pairs that actually ran} -- same
        WHERE net_return IS NOT NULL as get_consistency_by_strategy, so a
        consistency score is never shown without the sample size it's
        based on (S5/#1238: a 100% consistency off 2 tested stocks reads
        very differently than off 80)."""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT strategy_id, COUNT(*) AS tested
               FROM cross_stock_results
               WHERE net_return IS NOT NULL
               GROUP BY strategy_id"""
        )
        return {row["strategy_id"]: row["tested"] for row in cur.fetchall()}
