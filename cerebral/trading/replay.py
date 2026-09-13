"""
Replay engine for the Historical Replay campaign (REPLAY.md).

RP1: run_bars -- the single source of truth for "strategy code -> equity curve".
RP5: run_replay -- the portfolio-wide replay loop over cached historical bars.
"""
import logging

import pandas as pd

from cerebral.trading import news_cache
from cerebral.trading.gauntlet import compute_max_holding_days, _bars_per_year
from cerebral.trading.sandboxed_eval import evaluate_signals, evaluate_signals_verbose
from cerebral.trading.cost_model import Trade, compute_backtest_result

logger = logging.getLogger(__name__)


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


def run_bars_verbose(
    code: str, bars: pd.DataFrame, interval: str
) -> tuple[list[float], pd.Series, dict, "str | None"]:
    """Same as run_bars, but also returns the real sandbox failure reason
    (None on success), via evaluate_signals_verbose -- same pattern as SR1's
    evaluate_signals/evaluate_signals_verbose split. run_bars itself stays a
    thin wrapper around this so every existing caller's 3-tuple contract
    (plugins/scheduler.py's backtest closure, this module's own pre-RP5
    tests) is untouched; only run_replay (RP5) needs the reason.
    """
    signals, reason = evaluate_signals_verbose(code, bars)
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

    return list(equity), position, metrics, reason


def run_bars(code: str, bars: pd.DataFrame, interval: str) -> tuple[list[float], pd.Series, dict]:
    """Runs strategy `code` against `bars` via the sandboxed evaluator.

    Returns (equity_curve, position_series, metrics).  The position series is
    exposed (not just the equity curve) so callers can derive trades (RP2).
    """
    equity, position, metrics, _reason = run_bars_verbose(code, bars, interval)
    return equity, position, metrics


# ponytail: fixed per-interval warm-up margins, not derived from any one
# strategy's actual indicator length (unknowable without running it) -- a
# short replay window on a strategy with a real warm-up need would otherwise
# read as an all-flat CODE failure instead of correct warm-up behaviour.
# Deliberately NOT imported from live_tick.py's _lookback_days (RP5 spec):
# replay's data-fetch sizing is a separate decision from the live tick's.
def _warmup_days(interval: str) -> int:
    if interval == "1d":
        return 180
    if interval in ("1h", "4h"):
        return 60
    return 30  # 1m, 5m, 15m, 30m


def _compound(returns) -> float:
    """Cumulative return from a list of per-bar fractional returns -- same
    compounding cost_model.BacktestResult's own cumulative_*_return
    properties use, applied to a window-restricted slice rather than the
    full series."""
    total = 1.0
    for r in returns:
        total *= (1.0 + r)
    return total - 1.0


def run_replay(specs, start: str, end: str, bar_cache=None, replay_store=None) -> str:
    """Replays every spec in `specs` against cached historical bars over
    [start, end] and records one result per strategy. Returns the run_id.

    `specs` is a list of cerebral.trading.strategy_store.StrategySpec (the
    same dataclass live dispatch and run_gauntlet use) -- NOT run_gauntlet
    itself (see REPLAY.md D1): this is a thin per-strategy loop, not a
    six-gate statistical validation, so a short replay window doesn't
    trigger meaningless Monte Carlo/walk-forward gates, and nothing here
    writes to strategy_store or places a paper/live order.

    `bar_cache`/`replay_store` are injectable seams for testing, defaulting
    to the real cerebral.trading.bar_cache module and a real ReplayStore.
    """
    if bar_cache is None:
        from cerebral.trading import bar_cache
    if replay_store is None:
        from cerebral.trading.replay_store import ReplayStore
        replay_store = ReplayStore()

    # "mixed" -- specs may span more than one interval; there is no single
    # correct value for a run-level column when they do, and REPLAY.md's
    # schema doesn't track per-spec interval at the run level.
    intervals = {spec.interval for spec in specs}
    run_interval = intervals.pop() if len(intervals) == 1 else "mixed"
    run_id = replay_store.create_run(start, end, run_interval, len(specs))

    start_dt = pd.to_datetime(start)

    for spec in specs:
        try:
            margin_days = _warmup_days(spec.interval)
            fetch_start = (start_dt - pd.Timedelta(days=int(margin_days))).strftime("%Y-%m-%d")
            bars = bar_cache.get_bars(spec.symbol, fetch_start, end, spec.interval)

            # RP8: news_event_count is purely informational (REPLAY.md D4/
            # RP8 SAFETY: "must never gate, filter, or alter which strategies
            # get replayed or how their returns are computed") -- a failed
            # news fetch (network hiccup, no credentials, API shape change)
            # must never be mistaken for the STRATEGY failing, which is
            # exactly what would happen if this raised into the outer
            # except below. Isolated in its own try/except, defaulting to 0.
            news_count = 0
            try:
                news_cache.fetch_news(spec.symbol, fetch_start, end)
                for d in pd.date_range(start=start, end=end, freq="D"):
                    news_count += news_cache.count_news_events(spec.symbol, d.strftime("%Y-%m-%d"))
            except Exception as news_exc:
                logger.warning(
                    "[replay] news fetch failed for %s (%s), continuing with news_event_count=0: %s",
                    spec.strategy_id, spec.symbol, news_exc,
                )
                news_count = 0

            equity, position, metrics, reason = run_bars_verbose(spec.code, bars, spec.interval)

            if reason is not None:
                # A real code failure -- do not report a fabricated 0.0
                # return as if the strategy legitimately did nothing.
                replay_store.record_result(
                    run_id, spec.strategy_id, spec.symbol,
                    gross_return=None, net_return=None, n_trades=0,
                    max_drawdown=None, sharpe=None, flat_reason=reason,
                    news_event_count=0,
                )
                continue

            # Score only bars within the requested window -- the warm-up
            # margin exists so a strategy's indicator warm-up doesn't read as
            # a failure, not to include it in the reported performance.
            in_window = bars.index >= start_dt
            gross_window = [r for r, keep in zip(metrics["gross_returns"], in_window) if keep]
            net_window = [r for r, keep in zip(metrics["net_returns"], in_window) if keep]
            equity_window = [e for e, keep in zip(equity, in_window) if keep]
            trades = derive_trades(position, bars["Close"])
            n_trades_window = sum(
                1 for t in trades if in_window[t.index]
            ) if len(in_window) else 0

            gross_return = _compound(gross_window)
            net_return = _compound(net_window)

            if equity_window:
                running_max = equity_window[0]
                max_dd = 0.0
                for e in equity_window:
                    running_max = max(running_max, e)
                    max_dd = min(max_dd, (e / running_max) - 1.0 if running_max else 0.0)
            else:
                max_dd = 0.0

            if len(net_window) > 1:
                import numpy as np
                arr = np.array(net_window)
                sharpe = (
                    float(arr.mean() / arr.std() * (_bars_per_year(spec.interval) ** 0.5))
                    if arr.std() > 0 else 0.0
                )
            else:
                sharpe = 0.0

            replay_store.record_result(
                run_id, spec.strategy_id, spec.symbol,
                gross_return=gross_return, net_return=net_return,
                n_trades=n_trades_window, max_drawdown=max_dd, sharpe=sharpe,
                flat_reason=None,
                news_event_count=news_count,
            )
        except Exception as exc:
            # One broken strategy must never abort the whole replay run.
            logger.warning("[replay] %s (%s) failed: %s", spec.strategy_id, spec.symbol, exc, exc_info=True)
            replay_store.record_result(
                run_id, spec.strategy_id, spec.symbol,
                gross_return=None, net_return=None, n_trades=0,
                max_drawdown=None, sharpe=None, flat_reason=str(exc),
                news_event_count=0,
            )

    return run_id
