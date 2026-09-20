"""Regime stress windows and the risk view (#1251 follow-ups, 2026-09-19).

The 5-year cross-stock sweep is one bull market. A strategy that only works there has no edge worth
trusting, and a strategy that gives up little return while avoiding most of a crash IS valuable even
if it never beats buy-and-hold. This module scores each (strategy, stock) on several regimes --
the 2008 crash, the 2010s, the 2022 bear and the main 5-year window -- on BOTH return and drawdown.

Pure numpy/stdlib, no I/O. Informational only: nothing here may feed check_graduation,
check_retirement, run_gauntlet or auto_promote.

Cost: an explicit, dimensionally consistent charge per unit of position change, as a fraction of
traded notional. The platform is commission-free, so only spread + slippage remain: ~2 bps per side
on liquid US large caps. The gross (0 bps) return is stored alongside so the cost sensitivity is
visible rather than assumed. (The legacy cerebral.trading.cost_model charges by SHARE PRICE -- issue
#1329 -- and is deliberately not used here.)

ponytail: survivorship-biased universe (today's large caps) and correlated stocks -- every p-value is
optimistic and buy-and-hold is flattered. The permutation baseline is the rigorous cross-check.
"""
import datetime
import statistics
from typing import Dict, List, Optional, Sequence

import numpy as np

from cerebral.trading.cross_stock_stats import binom_upper_tail, bh_adjust

RESEARCH_COST = 0.0002   # 2 bps per side of traded notional: commission-free broker, spread + slippage only

# Regimes, [start, end). "main" is the sweep's own rolling 5-year window, computed at run time.
FIXED_WINDOWS = {
    "gfc": ("2007-10-01", "2010-01-01"),      # crash and recovery
    "mid": ("2010-01-01", "2020-01-01"),      # a decade incl. 2011, 2015-16 and 2018 drawdowns
    "bear22": ("2021-12-01", "2023-01-01"),   # rate-shock bear
}
MIN_STOCKS = 10                # stocks per (strategy, window) before it is scored
# "Defensive" = gives up at most this much return vs buy-and-hold while cutting drawdown by at least this much.
DEFENSIVE_MAX_LAG = 0.10
DEFENSIVE_DD_GAIN = 0.10


def windows(today: Optional[datetime.date] = None) -> Dict[str, tuple]:
    today = today or datetime.date.today()
    main = ((today - datetime.timedelta(days=365 * 5)).isoformat(), today.isoformat())
    return {**FIXED_WINDOWS, "main": main}


# Intraday history (Alpaca) starts 2016 but 5-minute bars are heavy, so research uses 2020 on. The "i:" prefix keeps
# these rows apart from the daily windows in strategy_stress.
INTRADAY_FIXED_WINDOWS = {
    "i:covid": ("2020-02-15", "2020-07-01"),     # crash and the retail-driven rebound
    "i:bull21": ("2021-01-01", "2022-01-01"),
    "i:bear22": ("2022-01-01", "2023-01-01"),
}


def intraday_windows(today: Optional[datetime.date] = None) -> Dict[str, tuple]:
    today = today or datetime.date.today()
    recent = ((today - datetime.timedelta(days=365 * 2)).isoformat(), today.isoformat())
    return {**INTRADAY_FIXED_WINDOWS, "i:recent": recent}


def _max_drawdown(net: np.ndarray) -> float:
    equity = np.cumprod(1.0 + net)
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity / peak - 1.0))


def evaluate_window(position, returns, cost: float = RESEARCH_COST) -> Optional[dict]:
    """position: the position held DURING each bar (already lagged), returns: simple per-bar returns,
    both sliced to the window. Entering a position at the window's first bar costs, like any other trade."""
    position = np.asarray(position, dtype=float)
    returns = np.asarray(returns, dtype=float)
    if len(position) != len(returns) or len(position) < 30:
        return None
    turns = np.abs(np.diff(position, prepend=0.0))
    gross = position * returns
    net = gross - turns * cost
    return {
        "net_return": float(np.prod(1.0 + net) - 1.0),
        "gross_return": float(np.prod(1.0 + gross) - 1.0),
        "benchmark_return": float(np.prod(1.0 + returns) - 1.0),
        "max_drawdown": _max_drawdown(net),
        "benchmark_max_drawdown": _max_drawdown(returns),
        "n_trades": int(np.count_nonzero(turns)),
    }


def _summarise_window(rows: Sequence[dict]) -> dict:
    n = len(rows)
    excess = [r["net_return"] - r["benchmark_return"] for r in rows]
    wins = sum(1 for e in excess if e > 0)
    return {
        "stocks": n,
        "beat_share": wins / n,
        "median_excess": statistics.median(excess),
        # max_drawdown is negative; a positive gain means the strategy fell less than buy-and-hold.
        "median_dd_gain": statistics.median(r["max_drawdown"] - r["benchmark_max_drawdown"] for r in rows),
        "p_value": binom_upper_tail(wins, n, 0.5),
    }


def summarize_intraday(rows_by_strategy: Dict[str, Dict[str, Sequence[dict]]], min_stocks: int = MIN_STOCKS) -> dict:
    """Day-trading rules are flat overnight, so buy-and-hold is the wrong yardstick. The test is absolute:
    does the rule make money AFTER costs, on most stocks, in EVERY regime? Per window: share of stocks with
    positive net return, median net and gross (the gap is the cost drag), a coin-flip p on positive/negative
    BH-adjusted across rules, and `profitable` = positive share >= 0.5 with positive median net in every window."""
    window_names = sorted({w for by_w in rows_by_strategy.values() for w in by_w})
    per: Dict[str, Dict[str, dict]] = {}
    for sid, by_w in rows_by_strategy.items():
        for w, rows in by_w.items():
            if len(rows) >= min_stocks:
                n = len(rows)
                wins = sum(1 for r in rows if r["net_return"] > 0)
                per.setdefault(sid, {})[w] = {
                    "stocks": n,
                    "positive_share": wins / n,
                    "median_net": statistics.median(r["net_return"] for r in rows),
                    "median_gross": statistics.median(r["gross_return"] for r in rows),
                    "median_trades": statistics.median(r["n_trades"] for r in rows),
                    "p_value": binom_upper_tail(wins, n, 0.5),
                }
    win_stats: Dict[str, dict] = {}
    for w in window_names:
        sids = [s for s in per if w in per[s]]
        for s, q in zip(sids, bh_adjust([per[s][w]["p_value"] for s in sids])):
            per[s][w]["q_value"] = q
            per[s][w]["significant"] = q < 0.05 and per[s][w]["median_net"] > 0
        win_stats[w] = {"ranked": len(sids), "significant": sum(1 for s in sids if per[s][w]["significant"])}
    out: List[dict] = []
    for sid, by_w in per.items():
        complete = all(w in by_w for w in window_names)
        profitable = complete and all(v["positive_share"] >= 0.5 and v["median_net"] > 0 for v in by_w.values())
        out.append({"strategy_id": sid, "windows": by_w, "profitable": profitable})
    out.sort(key=lambda r: (-int(r["profitable"]), -statistics.mean(v["median_net"] for v in r["windows"].values())))
    return {"windows": win_stats, "strategies": out, "profitable": sum(1 for r in out if r["profitable"])}


def summarize_stress(rows_by_strategy: Dict[str, Dict[str, Sequence[dict]]], min_stocks: int = MIN_STOCKS) -> dict:
    """rows_by_strategy: {strategy_id: {window: [per-stock dict from evaluate_window]}}.

    Returns {"windows": {window: {"ranked", "significant"}}, "strategies": [...], "robust": n, "defensive": n}
    where each strategy row carries per-window beat share / median excess / drawdown gain, a coin-flip
    p-value BH-adjusted across strategies within each window, and two flags:
      robust    -- beats buy-and-hold on >= half the stocks with positive median excess in EVERY window
      defensive -- in EVERY window: median excess >= -DEFENSIVE_MAX_LAG and median drawdown gain >= DEFENSIVE_DD_GAIN
    """
    window_names = sorted({w for by_w in rows_by_strategy.values() for w in by_w})
    per: Dict[str, Dict[str, dict]] = {}
    for sid, by_w in rows_by_strategy.items():
        for w, rows in by_w.items():
            if len(rows) >= min_stocks:
                per.setdefault(sid, {})[w] = _summarise_window(rows)

    win_stats: Dict[str, dict] = {}
    for w in window_names:
        sids = [s for s in per if w in per[s]]
        qs = bh_adjust([per[s][w]["p_value"] for s in sids])
        for s, q in zip(sids, qs):
            per[s][w]["q_value"] = q
            per[s][w]["significant"] = q < 0.05
        win_stats[w] = {"ranked": len(sids), "significant": sum(1 for s in sids if per[s][w]["significant"])}

    out: List[dict] = []
    for sid, by_w in per.items():
        complete = all(w in by_w for w in window_names)
        robust = complete and all(v["beat_share"] >= 0.5 and v["median_excess"] > 0 for v in by_w.values())
        defensive = complete and all(
            v["median_excess"] >= -DEFENSIVE_MAX_LAG and v["median_dd_gain"] >= DEFENSIVE_DD_GAIN
            for v in by_w.values()
        )
        out.append({"strategy_id": sid, "windows": by_w, "robust": robust, "defensive": defensive})
    out.sort(key=lambda r: (
        -int(r["robust"]), -int(r["defensive"]),
        -statistics.mean(v["median_excess"] for v in r["windows"].values()),
    ))
    return {
        "windows": win_stats,
        "strategies": out,
        "robust": sum(1 for r in out if r["robust"]),
        "defensive": sum(1 for r in out if r["defensive"]),
    }
