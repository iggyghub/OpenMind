from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from cerebral.trading.alerts import AlertDispatcher, StructuredAlert
from cerebral.trading.forward_record import ForwardRecord

@dataclass
class StrategyState:
    """Tracks the lifecycle state of a single trading strategy."""
    name: str
    status: str  # "paper", "live", "halted"
    live_trade_count: int = 0
    position_size_pct: float = 0.25
    worst_backtest_drawdown: float = 0.0
    live_equity_curve: List[float] = field(default_factory=list)
    promoted_at: Optional[datetime] = None
    peak_live_equity: float = 0.0


class StrategyLifecycle:
    """Manages paper-to-live graduation, position-size ramping, and automatic retirement."""

    def __init__(self, alert_dispatcher: Optional[AlertDispatcher] = None) -> None:
        self._states: Dict[str, StrategyState] = {}
        self._dispatcher = alert_dispatcher

    def get_state(self, name: str) -> StrategyState:
        if name not in self._states:
            self._states[name] = StrategyState(name=name, status="paper")
        return self._states[name]

    def update_live_fill(self, name: str, pnl: float) -> None:
        """Record a live fill and update equity curve / drawdown state."""
        state = self.get_state(name)
        if state.status == "halted":
            return

        state.live_equity_curve.append(pnl if not state.live_equity_curve else state.live_equity_curve[-1] + pnl)
        state.peak_live_equity = max(state.peak_live_equity, state.live_equity_curve[-1])
        state.live_trade_count += 1

    def check_graduation(self, name: str, record: ForwardRecord) -> bool:
        """Auto-promotes paper strategy to live when rolling CI excludes zero after 30+ trades."""
        state = self.get_state(name)
        if state.status != "paper":
            return False

        mean, lower, upper, is_sufficient = record.compute_expectancy_ci(strategy_id=name)
        if is_sufficient and lower > 0:
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
            return True
        return False

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
        if self._dispatcher:
            self._dispatcher.emit(StructuredAlert(
                severity="critical",
                event_type="strategy_retirement",
                message=f"Strategy '{name}' halted. Reason: {reason}",
                context={"strategy": name},
            ))

    def get_open_positions(self, name: str) -> List[dict]:
        """Returns pending/handled positions for a strategy (stubbed for panel view)."""
        state = self.get_state(name)
        return [{"symbol": name, "status": state.status, "live_trades": state.live_trade_count}]

    def get_alert_history(self) -> List[StructuredAlert]:
        """Returns all emitted alerts."""
        if self._dispatcher:
            return self._dispatcher.get_pending()
        return []
