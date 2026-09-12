"""
Replay engine for the Historical Replay campaign (REPLAY.md).

RP1: run_bars -- the single source of truth for "strategy code -> equity curve".
RP5 will add run_replay here.
"""
import pandas as pd

from cerebral.trading.gauntlet import compute_max_holding_days
from cerebral.trading.sandboxed_eval import evaluate_signals


def run_bars(code: str, bars: pd.DataFrame, interval: str) -> tuple[list[float], pd.Series, dict]:
    """Runs strategy `code` against `bars` via the sandboxed evaluator.

    Returns (equity_curve, position_series, metrics).  The position series is
    exposed (not just the equity curve) so callers can derive trades (RP2).
    """
    signals = evaluate_signals(code, bars)
    # Right-align short signal lists -- indicator warm-up means the strategy
    # may return fewer signals than bars; the LAST signal pairs with the LAST bar.
    if len(signals) < len(bars):
        signals = [0] * (len(bars) - len(signals)) + list(signals)
    signals = pd.Series(signals, index=bars.index)
    # Yesterday's decided position earns today's return.
    position = signals.shift(1).fillna(0.0)
    daily_returns = position * bars["Close"].pct_change().fillna(0.0)
    equity = 100.0 * (1.0 + daily_returns).cumprod()
    max_holding_days = compute_max_holding_days(position, interval)
    return list(equity), position, {"max_holding_days": max_holding_days}
