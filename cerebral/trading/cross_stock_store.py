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
from typing import Dict, List, Optional

from cerebral.paths import data_dir

DB_PATH = data_dir() / "cross_stock_results.db"

# F4 (#1249): minimum n_trades a pair must have to count as a qualifying vote.
# Seven trades over five years is noise (per S2 live-verify); 20 is a
# deliberate floor for a ~5-year window, not an inline magic number. Sized
# for '1d' -- see _min_trades_floor_for_interval below for how a faster
# interval scales it up.
MIN_TRADES_FLOOR = 20


def _min_trades_floor_for_interval(interval: Optional[str]) -> int:
    """Scale MIN_TRADES_FLOOR by how often the strategy actually gets a
    chance to trade (#1277 follow-up, 2026-09-17). MIN_TRADES_FLOOR was
    sized for '1d' over a ~5-year window; an intraday strategy gets that
    same '1d' quota of opportunities in days, not months, so the flat 20
    barely filters noise for it. Reuses gauntlet._bars_per_year -- the same
    bars-per-year table already used to annualize Sharpe per interval --
    as the one source of truth for "how much more often does this interval
    trade than 1d," rather than inventing a second one here.
    """
    from cerebral.trading.gauntlet import _bars_per_year
    scale = _bars_per_year(interval or "1d") / _bars_per_year("1d")
    return max(MIN_TRADES_FLOOR, round(MIN_TRADES_FLOOR * scale))


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
        # CAUSALITY C2: per-strategy look-ahead verdict (1 causal / 0 non-causal /
        # NULL untestable). Untestable rows still count as checked, so they are not
        # re-run every night.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS strategy_causality (
                strategy_id TEXT PRIMARY KEY,
                causal INTEGER,
                mismatches INTEGER NOT NULL,
                tested INTEGER NOT NULL,
                checked_at TEXT NOT NULL
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

    def get_consistency_by_strategy(
        self, interval_by_strategy: Optional[Dict[str, str]] = None,
    ) -> dict[str, float]:
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
        nothing to do with the strategy itself.

        `interval_by_strategy` (optional {strategy_id: interval}) applies
        the dynamic per-interval floor (_min_trades_floor_for_interval) to
        each PAIR's own n_trades -- a strategy missing from the map, or no
        map at all, keeps the flat MIN_TRADES_FLOOR ('1d' behavior), same
        as before this existed. The floor now varies per strategy, so it
        can't stay a single SQL bind param: MIN_TRADES_FLOOR still prunes
        in SQL first (cheap, and never wrongly excludes a row -- the
        dynamic floor is always >= it), the real per-strategy floor is
        applied in Python on what's left."""
        interval_by_strategy = interval_by_strategy or {}
        cur = self.conn.cursor()
        cur.execute(
            """SELECT strategy_id, net_return, n_trades
               FROM cross_stock_results
               WHERE net_return IS NOT NULL AND n_trades >= ?""",
            (MIN_TRADES_FLOOR,),
        )
        by_strategy: Dict[str, List[float]] = {}
        for row in cur.fetchall():
            sid = row["strategy_id"]
            floor = _min_trades_floor_for_interval(interval_by_strategy.get(sid))
            if row["n_trades"] < floor:
                continue
            by_strategy.setdefault(sid, []).append(row["net_return"])
        return {
            strategy_id: sum(1.0 for r in returns if r > 0) / len(returns)
            for strategy_id, returns in by_strategy.items()
        }

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

    def get_tested_count_by_strategy(
        self, interval_by_strategy: Optional[Dict[str, str]] = None,
    ) -> dict[str, int]:
        """Returns {strategy_id: count of pairs that actually ran} -- same
        WHERE net_return IS NOT NULL as get_consistency_by_strategy, so a
        consistency score is never shown without the sample size it's
        based on (S5/#1238: a 100% consistency off 2 tested stocks reads
        very differently than off 80).

        Same dynamic-floor convention as get_consistency_by_strategy (see
        its docstring) via the optional `interval_by_strategy` map."""
        interval_by_strategy = interval_by_strategy or {}
        cur = self.conn.cursor()
        cur.execute(
            """SELECT strategy_id, n_trades
               FROM cross_stock_results
               WHERE net_return IS NOT NULL AND n_trades >= ?""",
            (MIN_TRADES_FLOOR,),
        )
        counts: Dict[str, int] = {}
        for row in cur.fetchall():
            sid = row["strategy_id"]
            floor = _min_trades_floor_for_interval(interval_by_strategy.get(sid))
            if row["n_trades"] < floor:
                continue
            counts[sid] = counts.get(sid, 0) + 1
        return counts

    def get_pair_returns_by_strategy(
        self, interval_by_strategy: Optional[Dict[str, str]] = None,
    ) -> Dict[str, List[tuple]]:
        """{strategy_id: [(net_return, benchmark_return), ...]} for pairs that ran, have a
        buy-and-hold benchmark, clear the same dynamic trade floor as
        get_consistency_by_strategy, and belong to a strategy the causality gate has not marked
        as reading future bars. A NULL benchmark is excluded, never counted as a loss."""
        interval_by_strategy = interval_by_strategy or {}
        non_causal = self.get_non_causal_ids()
        cur = self.conn.cursor()
        cur.execute(
            """SELECT strategy_id, net_return, benchmark_return, n_trades
               FROM cross_stock_results
               WHERE net_return IS NOT NULL AND benchmark_return IS NOT NULL AND n_trades >= ?""",
            (MIN_TRADES_FLOOR,),
        )
        out: Dict[str, List[tuple]] = {}
        for row in cur.fetchall():
            sid = row["strategy_id"]
            if sid in non_causal:
                continue
            if row["n_trades"] < _min_trades_floor_for_interval(interval_by_strategy.get(sid)):
                continue
            out.setdefault(sid, []).append((row["net_return"], row["benchmark_return"]))
        return out

    def record_causality(self, strategy_id, causal: Optional[bool], mismatches: int, tested: int) -> None:
        causal_val = None if causal is None else 1 if causal else 0
        checked_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO strategy_causality (strategy_id, causal, mismatches, tested, checked_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(strategy_id) DO UPDATE SET
                   causal=excluded.causal,
                   mismatches=excluded.mismatches,
                   tested=excluded.tested,
                   checked_at=excluded.checked_at""",
            (strategy_id, causal_val, mismatches, tested, checked_at),
        )
        self.conn.commit()

    def get_causality_checked_ids(self) -> set:
        cur = self.conn.cursor()
        cur.execute("SELECT strategy_id FROM strategy_causality")
        return {row["strategy_id"] for row in cur.fetchall()}

    def get_non_causal_ids(self) -> set:
        cur = self.conn.cursor()
        cur.execute("SELECT strategy_id FROM strategy_causality WHERE causal = 0")
        return {row["strategy_id"] for row in cur.fetchall()}
