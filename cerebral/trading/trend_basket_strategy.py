"""Hand-written (not LLM-generated) strategy code for the trend-basket play (ADR-0038).

Like the IPO play, this code is registered directly, without a per-symbol Gauntlet run
(ADR-0038 amendment 2026-09-24): the Gauntlet's one-symbol, one-year backtest can't see this
strategy's edge (breadth timing + cross-sectional selection) and rejected 98% of real picks.

Exit is a flat, non-tightening 12% trailing stop from the peak since entry, hard-capped at bar 20
(entry is bar 0). Deliberately NOT the IPO play's tighten-to-1%-after-+20% ratchet -- that shape
was tested against ordinary multi-day swing holds and found to destroy this strategy's edge, at
both daily and 5-minute granularity (see the ADR's "Why the exit isn't the IPO play's exit").

Same stop-check-before-peak-update ordering ipo_strategy.py uses (fixed there 2026-09-03 after a
same-bar-inflated-peak bug): the stop check for bar i uses the peak as of the END of bar i-1, so a
bar's own high can never retroactively save that same bar's own low from a stop it should have
tripped.
"""

import re

# Entry is anchored to a real calendar date (2026-09-24 fix). The original code treated data's
# FIRST bar as the entry, but live_tick hands every strategy a 180-day window and acts on the LAST
# signal -- always past bar 20, so always flat: the basket could never open a position.
_TEMPLATE = '''ENTRY = "__ENTRY__"

def strategy(data) -> list:
    start = None
    for i in range(len(data)):
        if str(data.index[i])[:10] >= ENTRY:
            start = i
            break
    if start is None:
        return [0] * len(data)
    signals = [0] * start
    peak = data["Open"].iloc[start]
    stopped_out = False
    for i in range(start, len(data)):
        if stopped_out or i - start > 20:
            signals.append(0)
            continue
        low_i = data["Low"].iloc[i]
        high_i = data["High"].iloc[i]
        stop_price = peak * 0.88
        if low_i <= stop_price:
            stopped_out = True
            signals.append(0)
        else:
            signals.append(1)
        if high_i > peak:
            peak = high_i
    return signals
'''

_ENTRY_RE = re.compile(r'^ENTRY = "(\d{4}-\d{2}-\d{2})"', re.M)


def trend_basket_code(entry_date: str) -> str:
    """Strategy source for one basket position entered on `entry_date` (YYYY-MM-DD)."""
    return _TEMPLATE.replace("__ENTRY__", entry_date)


def entry_date_of(code: str):
    """The ENTRY date embedded by trend_basket_code, or None for any other code."""
    m = _ENTRY_RE.search(code or "")
    return m.group(1) if m else None
