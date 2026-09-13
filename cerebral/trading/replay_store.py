import sqlite3
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from cerebral.paths import data_dir

DB_PATH = data_dir() / "replay_runs.db"

class ReplayStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DB_PATH)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS replay_runs (
                run_id TEXT PRIMARY KEY,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                interval TEXT NOT NULL,
                n_strategies INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS replay_results (
                run_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                gross_return REAL,
                net_return REAL,
                n_trades INTEGER,
                max_drawdown REAL,
                sharpe REAL,
                flat_reason TEXT,
                PRIMARY KEY (run_id, strategy_id)
            );
        """)
        self.conn.commit()

    def create_run(self, start: str, end: str, interval: str, n_strategies: int) -> str:
        run_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO replay_runs (run_id, start, end, interval, n_strategies, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, start, end, interval, n_strategies, created_at),
        )
        self.conn.commit()
        return run_id

    def record_result(
        self,
        run_id: str,
        strategy_id: str,
        symbol: str,
        gross_return: float,
        net_return: float,
        n_trades: int,
        max_drawdown: float,
        sharpe: float,
        flat_reason: Optional[str],
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO replay_results (
                run_id, strategy_id, symbol, gross_return, net_return, n_trades, max_drawdown, sharpe, flat_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, strategy_id) DO UPDATE SET
                symbol=excluded.symbol,
                gross_return=excluded.gross_return,
                net_return=excluded.net_return,
                n_trades=excluded.n_trades,
                max_drawdown=excluded.max_drawdown,
                sharpe=excluded.sharpe,
                flat_reason=excluded.flat_reason""",
            (run_id, strategy_id, symbol, gross_return, net_return, n_trades, max_drawdown, sharpe, flat_reason),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM replay_runs WHERE run_id = ?", (run_id,))
        return cur.fetchone()

    def get_results(self, run_id: str) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM replay_results WHERE run_id = ?", (run_id,))
        return cur.fetchall()

    def list_runs(self, limit: Optional[int] = None) -> List[sqlite3.Row]:
        cur = self.conn.cursor()
        if limit is not None:
            cur.execute("SELECT * FROM replay_runs ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            cur.execute("SELECT * FROM replay_runs ORDER BY created_at DESC")
        return cur.fetchall()

    def close(self) -> None:
        self.conn.close()
