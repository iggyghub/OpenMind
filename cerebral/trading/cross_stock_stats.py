"""Benchmark-relative cross-stock statistics (#1250 decision 2026-09-19, #1251 axis 3).

Pure functions, no I/O. Informational only: nothing here may feed check_graduation,
check_retirement, run_gauntlet or auto_promote.

The question is "does this rule beat buy-and-hold on more stocks than a coin flip would?" so
the null is P(beat) = 0.5 -- NOT the sweep-wide base rate. Against the base rate (~0.29 here:
most strategies lose to buy-and-hold after costs) a near-clone of buy-and-hold that ties on half
the stocks looks 'significant', which is exactly wrong.

ponytail: pairs are treated as independent. The 100 basket stocks share one regime, so the
effective sample size is far smaller and every p-value here is optimistic (too small); a
'significant' flag is therefore a necessary, not sufficient, condition. The permutation baseline
(#1251 axis 2) is the rigorous fix.
"""
import statistics
from math import comb
from typing import Dict, List, Optional, Sequence, Tuple

MIN_STOCKS = 20  # fewer tested stocks than this and a share/p-value is not worth ranking

CAVEAT = (
    "Excess vs buy-and-hold over ~5 years, look-ahead screened. Stocks are correlated, so "
    "p/q-values are optimistic. Informational only."
)


def binom_upper_tail(k: int, n: int, q: float = 0.5) -> float:
    """P(X >= k) for X ~ Binomial(n, q)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(comb(n, i) * q ** i * (1 - q) ** (n - i) for i in range(k, n + 1))


def bh_adjust(pvalues: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg q-values, returned in the input order (monotone, capped at 1)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = running
    return q


def summarize_vs_benchmark(
    pairs_by_strategy: Dict[str, Sequence[Tuple[float, float]]],
    min_stocks: int = MIN_STOCKS,
) -> List[dict]:
    """pairs_by_strategy: {strategy_id: [(net_return, benchmark_return), ...]} -- one entry per
    tested stock. Returns one row per strategy with >= min_stocks stocks, ranked by share of
    stocks that beat buy-and-hold, then median excess return, with a coin-flip sign-test
    p-value and a Benjamini-Hochberg q-value computed across every ranked strategy (the trial
    count, so 'we found N winners' can be told apart from 'N is what chance predicts')."""
    rows: List[dict] = []
    for sid, pairs in pairs_by_strategy.items():
        n = len(pairs)
        if n < min_stocks:
            continue
        wins = sum(1 for net, bench in pairs if net > bench)
        rows.append({
            "strategy_id": sid,
            "stocks_tested": n,
            "beat_share": wins / n,
            "median_excess": statistics.median(net - bench for net, bench in pairs),
            "p_value": binom_upper_tail(wins, n, 0.5),
        })
    qs = bh_adjust([r["p_value"] for r in rows])
    for r, q in zip(rows, qs):
        r["q_value"] = q
        r["significant"] = q < 0.05
    rows.sort(key=lambda r: (-r["beat_share"], -r["median_excess"]))
    return rows


PERM_CAVEAT = (
    "Random-timing null: same trades, holding periods and exposure, shifted to arbitrary dates; 5 bps "
    "cost on both sides. Stocks are correlated, so the combined p is still optimistic. Informational only."
)


def summarize_permutation(pvalues_by_strategy: Dict[str, Sequence[float]], min_stocks: int = 3) -> List[dict]:
    """One row per strategy tested on >= min_stocks stocks. The strategy's p-value is Bonferroni-
    combined over its own stocks (min p x number of stocks, capped at 1) so trying several stocks
    is not a free lottery, then Benjamini-Hochberg across every strategy. Sorted by q-value."""
    rows: List[dict] = []
    for sid, ps in pvalues_by_strategy.items():
        k = len(ps)
        if k < min_stocks:
            continue
        rows.append({
            "strategy_id": sid,
            "stocks_tested": k,
            "best_p": min(ps),
            "p_value": min(1.0, k * min(ps)),
        })
    qs = bh_adjust([r["p_value"] for r in rows])
    for r, q in zip(rows, qs):
        r["q_value"] = q
        r["significant"] = q < 0.05
    rows.sort(key=lambda r: (r["q_value"], r["best_p"]))
    return rows
