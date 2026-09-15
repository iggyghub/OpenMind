"""Cross-stock pattern-generalization backtest (CROSS-STOCK-VALIDATION S2,
#1235). run_pair is the single-pair analogue of replay.py's run_replay
per-spec body -- same computation (run_bars_verbose, compounded returns,
running-peak drawdown), but against one arbitrary symbol over one fixed
window, not the spec's own registered symbol chunked by month. Reuses
run_bars_verbose (sandboxed strategy execution), NOT run_gauntlet -- same
reasoning as REPLAY.md D1 / BATCH-REPLAY's SAFETY section: run_gauntlet's
auto_promote must never be reachable from a pure-replay path.
"""
import logging
from typing import Optional

import pandas as pd

from cerebral.trading.replay import _compound, _warmup_days, derive_trades, run_bars_verbose

logger = logging.getLogger(__name__)


def run_pair(strategy_id: str, code: str, symbol: str, start: str, end: str,
             interval: str = "1d", bar_cache=None) -> dict:
    """Backtests `code` against `symbol`'s bars over [start, end]. Returns
    {"net_return", "max_drawdown", "n_trades", "flat_reason"} -- flat_reason
    is non-None on a real sandbox failure (bad code, no bars), in which case
    the other fields are None, never a fabricated 0.0 (same convention as
    run_replay: a strategy that couldn't run is not the same as one that
    ran and did nothing)."""
    if bar_cache is None:
        from cerebral.trading import bar_cache

    try:
        margin_days = _warmup_days(interval)
        start_dt = pd.to_datetime(start)
        fetch_start = (start_dt - pd.Timedelta(days=int(margin_days))).strftime("%Y-%m-%d")
        bars = bar_cache.get_bars(symbol, fetch_start, end, interval)
    except Exception as exc:
        logger.warning("[cross_stock] %s/%s: bar fetch failed: %s", strategy_id, symbol, exc)
        return {"net_return": None, "max_drawdown": None, "n_trades": 0, "flat_reason": str(exc)}

    equity, position, metrics, reason = run_bars_verbose(code, bars, interval)
    if reason is not None:
        return {"net_return": None, "max_drawdown": None, "n_trades": 0, "flat_reason": reason}

    in_window = bars.index >= start_dt
    net_window = [r for r, keep in zip(metrics["net_returns"], in_window) if keep]
    equity_window = [e for e, keep in zip(equity, in_window) if keep]
    trades = derive_trades(position, bars["Close"])
    n_trades = sum(1 for t in trades if in_window[t.index]) if len(in_window) else 0

    net_return = _compound(net_window)

    if equity_window:
        running_max = equity_window[0]
        max_dd = 0.0
        for e in equity_window:
            running_max = max(running_max, e)
            max_dd = min(max_dd, (e / running_max) - 1.0 if running_max else 0.0)
    else:
        max_dd = 0.0

    return {"net_return": net_return, "max_drawdown": max_dd, "n_trades": n_trades, "flat_reason": None}


def build_pairs(specs: list, basket: list[str]) -> list[tuple]:
    """Cross-product of every cross_test_eligible spec against every basket
    symbol, as (spec, symbol) tuples in a stable order -- the resumable
    cursor is just an index into this list, so the order must be
    deterministic across restarts (specs already come out of
    StrategyStore.list_all() ordered by created_at; basket is a fixed
    module-level list)."""
    return [(spec, symbol) for spec in specs if spec.cross_test_eligible for symbol in basket]
