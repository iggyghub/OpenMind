from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from cerebral.paths import data_dir
from cerebral.trading.alerts import AlertDispatcher, StructuredAlert
from cerebral.trading.forward_record import ForwardRecord

if TYPE_CHECKING:
    from cerebral.trading.discovery import VettedTickers

# S28 (#881): symbol -> accession number (or None if no filing found);
# symbol -> (red_flagged, reason). Both sync -- check_graduation's whole
# call chain (dispatch_due_events -> _apply_lifecycle) is synchronous,
# offloaded to a worker thread via asyncio.to_thread in _scheduler_loop, so
# a blocking SEC/LLM call here is safe the same way run_strategy_tick's own
# blocking yfinance fetch already is. Production wires a sync bridge over
# the real (async) StocksPlugin; tests inject plain sync fakes.
LatestAccessionFn = Callable[[str], Optional[str]]
FundamentalsScanFn = Callable[[str], "tuple[bool, str]"]

_DEFAULT_DB_PATH = data_dir() / "lifecycle.sqlite"

@dataclass
class StrategyState:
    """Tracks the lifecycle state of a single trading strategy."""
    name: str
    status: str  # "paper", "live", "halted"
    live_trade_count: int = 0
    position_size_pct: float = 0.25
    live_equity_curve: List[float] = field(default_factory=list)
    promoted_at: Optional[datetime] = None
    peak_live_equity: float = 0.0


class StrategyLifecycle:
    """Manages paper-to-live graduation, position-size ramping, and automatic
    retirement. State (status, ramp, live equity curve) is SQLite-backed --
    a Felix restart must not silently revert a graduated strategy back to
    "paper" (S12/#856) -- following the same db_path-injection convention
    as ForwardRecord/StrategyStore so tests stay isolated (each test gets
    its own tmp_path, never the shared production db)."""

    def __init__(
        self, alert_dispatcher: Optional[AlertDispatcher] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self._states: Dict[str, StrategyState] = {}
        self._dispatcher = alert_dispatcher
        self._db_path = db_path if db_path is not None else _DEFAULT_DB_PATH
        self._init_db()
        self._load_states()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_states (
                name TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'paper',
                live_trade_count INTEGER NOT NULL DEFAULT 0,
                position_size_pct REAL NOT NULL DEFAULT 0.25,
                live_equity_curve TEXT NOT NULL DEFAULT '[]',
                promoted_at TEXT,
                peak_live_equity REAL NOT NULL DEFAULT 0.0
            )
        """)
        conn.commit()
        conn.close()

    def _load_states(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM strategy_states").fetchall()
        conn.close()
        for row in rows:
            self._states[row["name"]] = StrategyState(
                name=row["name"],
                status=row["status"],
                live_trade_count=row["live_trade_count"],
                position_size_pct=row["position_size_pct"],
                live_equity_curve=json.loads(row["live_equity_curve"]),
                promoted_at=datetime.fromisoformat(row["promoted_at"]) if row["promoted_at"] else None,
                peak_live_equity=row["peak_live_equity"],
            )

    def _save_state(self, state: StrategyState) -> None:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            INSERT OR REPLACE INTO strategy_states
            (name, status, live_trade_count, position_size_pct, live_equity_curve, promoted_at, peak_live_equity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            state.name, state.status, state.live_trade_count, state.position_size_pct,
            json.dumps(state.live_equity_curve),
            state.promoted_at.isoformat() if state.promoted_at else None,
            state.peak_live_equity,
        ))
        conn.commit()
        conn.close()

    def get_state(self, name: str) -> StrategyState:
        if name not in self._states:
            state = StrategyState(name=name, status="paper")
            self._states[name] = state
            self._save_state(state)
        return self._states[name]

    def update_live_fill(self, name: str, pnl: float) -> None:
        """Record a live fill and update equity curve / drawdown state."""
        state = self.get_state(name)
        if state.status == "halted":
            return

        state.live_equity_curve.append(pnl if not state.live_equity_curve else state.live_equity_curve[-1] + pnl)
        state.peak_live_equity = max(state.peak_live_equity, state.live_equity_curve[-1])
        state.live_trade_count += 1
        self._save_state(state)

    def check_graduation(
        self, name: str, record: ForwardRecord,
        symbol: Optional[str] = None,
        latest_accession_fn: Optional["LatestAccessionFn"] = None,
        fundamentals_scan_fn: Optional["FundamentalsScanFn"] = None,
        vetted_tickers: Optional["VettedTickers"] = None,
    ) -> bool:
        """Auto-promotes paper strategy to live when rolling CI excludes zero after 30+ trades.

        S28 (#881), decision #45: for a never-before-traded ticker only,
        right at this paper->live moment (never at idea-sourcing time, never
        blocking paper trading), pulls the ticker's latest 10-Q/10-K and
        LLM-scans it for red-flag language before allowing the promotion.
        "Never-before-traded" is exactly what `vetted_tickers` already
        tracks: a symbol with no vetting record (or one on a since-
        superseded filing) hasn't been cleared for THIS filing yet, so it
        gets a real scan; a symbol already vetted clean on the CURRENT
        filing skips straight through without a repeat SEC/LLM call.

        Entirely skipped (backward compatible) when `symbol` is None or the
        gate functions aren't supplied -- existing callers/tests that don't
        care about this feature are unaffected.
        """
        state = self.get_state(name)
        if state.status != "paper":
            return False

        mean, lower, upper, is_sufficient, trade_count, distinct_days = record.compute_expectancy_ci(strategy_id=name)
        if not (is_sufficient and lower > 0):
            return False

        if symbol is not None and latest_accession_fn is not None and fundamentals_scan_fn is not None:
            accession = latest_accession_fn(symbol)
            if accession is not None:
                cached = vetted_tickers.get_verdict(symbol, accession) if vetted_tickers is not None else None
                if cached is not None:
                    red_flagged, reason = cached, "cached verdict from a prior vetting of this exact filing"
                else:
                    red_flagged, reason = fundamentals_scan_fn(symbol)
                    if vetted_tickers is not None:
                        vetted_tickers.record(symbol, accession, red_flagged)
                if red_flagged:
                    if self._dispatcher:
                        self._dispatcher.emit(StructuredAlert(
                            severity="critical",
                            event_type="fundamentals_red_flag",
                            message=f"Strategy '{name}' graduation refused: {reason}",
                            context={"strategy": name, "symbol": symbol, "accession": accession},
                        ))
                    return False
            # accession is None: no filing found -- conservative-continue,
            # do not block graduation over a missing/unreachable filing
            # (inventing a refusal here would be a fabricated signal, the
            # same failure class this campaign has caught before).

        state.status = "live"
        state.live_trade_count = 0
        state.position_size_pct = 0.25
        state.live_equity_curve = []
        state.peak_live_equity = 0.0
        state.promoted_at = datetime.now(timezone.utc)

        if self._dispatcher:
            self._dispatcher.emit(StructuredAlert(
                severity="info",
                event_type="paper_to_live_graduation",
                message=f"Strategy '{name}' graduated to live trading (25% size).",
                context={"strategy": name},
            ))
        self._save_state(state)
        return True

    def apply_position_ramp(self, name: str) -> float:
        """Returns current position size percentage based on live trade count."""
        state = self.get_state(name)
        if state.status != "live":
            return state.position_size_pct

        count = state.live_trade_count
        if count < 30:
            state.position_size_pct = 0.25
        elif count < 60:
            state.position_size_pct = 0.50
        else:
            state.position_size_pct = 1.0
        self._save_state(state)
        return state.position_size_pct

    def check_retirement(self, name: str, worst_backtest_dd: float) -> bool:
        """Halts strategy if rolling live CI re-enters zero or drawdown breaches 2x backtest worst."""
        state = self.get_state(name)
        if state.status != "live":
            return False

        # 1. Rolling CI check (last 30+ live trades)
        if len(state.live_equity_curve) >= 30:
            per_trade_pnls = np.diff(state.live_equity_curve, prepend=0.0)
            recent_pnls = per_trade_pnls[-30:]
            if len(recent_pnls) > 1:
                mean = float(np.mean(recent_pnls))
                se = float(np.std(recent_pnls, ddof=1) / np.sqrt(len(recent_pnls)))
                lower_ci = mean - 1.96 * se
                if lower_ci <= 0:
                    self._halt_strategy(name, "Rolling live CI re-entered zero over last 30 trades")
                    return True

        # 2. Drawdown breach check
        current_live_dd = state.peak_live_equity - state.live_equity_curve[-1] if state.live_equity_curve else 0.0
        if worst_backtest_dd > 0 and current_live_dd > 2.0 * worst_backtest_dd:
            self._halt_strategy(name, f"Live drawdown {current_live_dd:.2f} exceeds 2x worst backtest drawdown ({2.0 * worst_backtest_dd:.2f})")
            return True

        return False

    def _halt_strategy(self, name: str, reason: str) -> None:
        state = self.get_state(name)
        state.status = "halted"
        self._save_state(state)
        if self._dispatcher:
            self._dispatcher.emit(StructuredAlert(
                severity="critical",
                event_type="strategy_retirement",
                message=f"Strategy '{name}' halted. Reason: {reason}",
                context={"strategy": name},
            ))

    def halt_strategy(self, name: str, reason: str = "Halted by user") -> None:
        """Thin public wrapper around _halt_strategy."""
        self._halt_strategy(name, reason)

    def resume_strategy(self, name: str) -> None:
        """Resumes a halted strategy back to 'paper' (never straight to 'live')."""
        state = self.get_state(name)
        state.status = "paper"
        self._save_state(state)

    def get_open_positions(self, name: str) -> List[dict]:
        """Returns pending/handled positions for a strategy (stubbed for panel view)."""
        state = self.get_state(name)
        return [{"symbol": name, "status": state.status, "live_trades": state.live_trade_count}]

    def get_alert_history(self) -> List[StructuredAlert]:
        """Returns all emitted alerts."""
        if self._dispatcher:
            return self._dispatcher.get_pending()
        return []
