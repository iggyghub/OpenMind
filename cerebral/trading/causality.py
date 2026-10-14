import numpy as np
from dataclasses import dataclass
from typing import Callable, Optional

@dataclass(frozen=True)
class CausalityResult:
    causal: Optional[bool]   # True / False, or None when the strategy could not be tested
    mismatches: int          # cut points where the truncated run disagreed with the full run
    tested: int              # cut points that produced a conclusive comparison


# Measured 2026-09-19: 11 cut points passed two Force Index strategies that 60 cut points
# caught leaking (6/60 and 8/60 mismatches on AAPL) -- a subtle leak only shows on a few bars.
DEFAULT_CUTS = 60


def check_causality(code, bars, evaluate=None, n_cuts=DEFAULT_CUTS, min_bars=600) -> CausalityResult:
    if evaluate is None:
        from cerebral.trading.sandboxed_eval import evaluate_signals_verbose
        evaluate = evaluate_signals_verbose

    try:
        full, reason = evaluate(code, bars)
    except Exception:
        return CausalityResult(None, 0, 0)

    if reason is not None and "lookahead" in reason.lower():
        # sandboxed_eval's static guard (shift(-N)) already proved a future read.
        return CausalityResult(False, 1, 1)
    if reason is not None or not any(full):
        return CausalityResult(None, 0, 0)

    if len(bars) < min_bars + n_cuts:
        return CausalityResult(None, 0, 0)

    cuts = np.linspace(min_bars, len(bars) - 1, n_cuts).astype(int)
    cuts = sorted(set(cuts))

    mismatches = 0
    tested = 0

    for c in cuts:
        try:
            part, r = evaluate(code, bars.iloc[:c])
        except Exception:
            continue

        if r is not None:
            continue

        tested += 1
        if len(part) != c or part[c - 1] != full[c - 1]:
            mismatches += 1
            break  # one future-dependent signal is enough; leaky strategies stay cheap

    if tested == 0:
        return CausalityResult(None, 0, 0)

    return CausalityResult(mismatches == 0, mismatches, tested)
