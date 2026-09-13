"""
Replay engine for the Historical Replay campaign (REPLAY.md).

RP1: run_bars -- the single source of truth for "strategy code -> equity curve".
RP5 will add run_replay here.
"""
import pandas as pd

from cerebral.trading.gauntlet import compute_max_holding_days
from cerebral.trading.sandboxed_eval import evaluate_signals
from cerebral.trading.cost_model import Trade, compute_backtest_result


def derive_trades(position: pd.Series, close: pd.Series) -> list[Trade]:
    """Derive a list of Trade objects from position changes and close prices.

    The first bar counts as a change from an implicit flat (0) position, not
    from NaN -- ``position.diff()``'s first element is always NaN regardless
    of the actual starting position, and ``NaN != 0`` is True in Python, so a
    raw ``.diff()`` spuriously "trades" on bar 0 every single time, even for a
    position that never changes. Tracking `prev` by hand avoids that.
    """
    trades: list[Trade] = []
    prev = 0.0
    for pos_idx, idx in enumerate(position.index):
        cur = position.loc[idx]
        delta = cur - prev
        if delta != 0:
            direction = "buy" if delta > 0 else "sell"
            price = float(close.loc[idx])
            trades.append(
                Trade(
                    index=pos_idx,
                    direction=direction,
                    price=price,
                    value=abs(delta) * price,
                )
            )
        prev = cur
    return trades


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
    
    # RP2: derive trades and compute net-of-cost returns. compute_backtest_result
    # wants per-bar FRACTIONAL returns (its cumulative_net_return property
    # compounds via (1+r), which only makes sense for returns, not price
    # levels) -- pass daily_returns, not the cumulative equity curve. It also
    # returns a BacktestResult dataclass (cerebral/trading/cost_model.py), not
    # a dict -- .net_returns/.gross_returns are the real attributes.
    trades = derive_trades(position, bars["Close"])
    cost_config = {}  # Zero-cost baseline; reuse gauntlet default convention
    net_result = compute_backtest_result(list(daily_returns), trades, cost_config)
    metrics = {
        "max_holding_days": max_holding_days,
        "net_returns": net_result.net_returns,
        "gross_returns": net_result.gross_returns,
    }
    
    return list(equity), position, metrics
