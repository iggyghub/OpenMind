"""Random-timing null for a strategy's position series (#1251 axis 2).

Question answered: "is this strategy better than the SAME exposure at random times?" A strategy
that cannot beat its own position pattern slid to arbitrary dates has no timing edge -- it is
either buy-and-hold in disguise or luck. This is the rigorous version of the missing benchmark:
buy-and-hold only says "beat holding the stock"; this says "beat random timing with identical
trade count, holding periods, direction mix and exposure".

Method: circular time-shift permutation. The position series is rolled against the return series
by many random offsets. Every rolled series has exactly the same trades/durations/exposure as the
real one; only the alignment with price moves is destroyed.

Pure numpy, no I/O. Informational only -- nothing here may feed check_graduation,
check_retirement, run_gauntlet or auto_promote.

Cost: ONE explicit, dimensionally consistent cost (a fraction of traded notional per unit of
position change), applied identically to the strategy and to every null draw, so the p-value never
depends on cerebral.trading.cost_model (whose per-trade charge scales with SHARE PRICE, not
notional -- filed separately).

ponytail: a single symbol at a time, so p-values across a strategy's stocks are correlated
(same market regime); the caller combines them conservatively. Add a block bootstrap if
circular shifts prove too coarse for very long holding periods.
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np

DEFAULT_COST = 0.0005   # 5 bps of traded notional per unit of position change (spread + slippage + fees)
DEFAULT_SIMS = 500
MIN_BARS = 100


@dataclass(frozen=True)
class NullResult:
    observed: float      # compounded net return of the real position series
    null_median: float   # median compounded net return over the shifted draws
    p_value: float       # (1 + #draws >= observed) / (n_sims + 1): P(random timing does at least this well)
    n_sims: int


def _compound(position: np.ndarray, returns: np.ndarray, cost: float) -> float:
    trades = np.abs(np.diff(position, prepend=0.0))
    net = position * returns - trades * cost
    return float(np.prod(1.0 + net) - 1.0)


def circular_shift_null(
    position, returns, cost: float = DEFAULT_COST, n_sims: int = DEFAULT_SIMS, seed: int = 0,
) -> Optional[NullResult]:
    """position: the position held DURING each bar (already lagged, e.g. signals.shift(1));
    returns: each bar's simple return. Returns None when there is too little data or the
    strategy never holds a position (nothing to test)."""
    position = np.asarray(position, dtype=float)
    returns = np.asarray(returns, dtype=float)
    n = len(position)
    if n != len(returns) or n < MIN_BARS or not np.any(position):
        return None

    observed = _compound(position, returns, cost)
    # Keep shifts away from 0 (and from n) so a draw is never nearly the real alignment.
    lo = max(20, n // 20)
    candidates = np.arange(lo, n - lo)
    if len(candidates) == 0:
        return None
    rng = np.random.default_rng(seed)
    shifts = rng.choice(candidates, size=min(n_sims, len(candidates)), replace=False)
    null = np.array([_compound(np.roll(position, int(k)), returns, cost) for k in shifts])
    p = (1 + int(np.sum(null >= observed))) / (len(null) + 1)
    return NullResult(observed, float(np.median(null)), p, len(null))
