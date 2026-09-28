"""Hand-written (not LLM-generated) strategy code for IPO pop-then-fade plays.

Not routed through to_strategy -- see cerebral/trading/discovery.py's IPO-calendar dispatch
path, which registers this code directly via StrategyStore.save(), bypassing _run_gauntlet's
per-symbol backtest (a brand-new IPO ticker has no price history to backtest against before
its first trading day -- see CONTEXT.md's "IPO play" glossary entry for the full reasoning).
This module's own code was validated once via a real _run_gauntlet call against historical
IPO tickers before being adopted -- not on every new ticker it gets applied to.

2026-09-03: the original version checked a bar's own Low against a peak/stop already
inflated by that SAME bar's own High, so a single volatile bar (routine on real IPO opens --
confirmed live against BRVE/ATTO/LTGO, all of which have a >4% Open-to-High range on their
very first 5-minute bar alone) could trip the stop before the position ever meaningfully
held. Fixed: the stop check for bar i now uses the peak as of the END of bar i-1 (peak/
tight_armed only update AFTER that bar's own check) -- a bar's own high can no longer
retroactively trip its own low's stop-check.
"""

# 2026-09-24 (#1351): entry is anchored to the IPO date, not data's first bar. live_tick hands a 5m
# strategy a sliding 30-day window; once it slid past IPO day the old code re-anchored to an
# arbitrary bar, reset stopped_out and re-entered. The window no longer holding the IPO day means
# the play is over: flat.
_TEMPLATE = '''ENTRY = "__ENTRY__"

def strategy(data) -> list:
    if len(data) == 0 or str(data.index[0])[:10] > ENTRY:
        return [0] * len(data)
    start = None
    for i in range(len(data)):
        if str(data.index[i])[:10] >= ENTRY:
            start = i
            break
    if start is None:
        return [0] * len(data)
    signals = [0] * start
    entry_price = data["Open"].iloc[start]
    peak = entry_price
    tight_armed = False
    stopped_out = False
    for i in range(start, len(data)):
        if stopped_out:
            signals.append(0)
            continue
        low_i = data["Low"].iloc[i]
        high_i = data["High"].iloc[i]
        trail_pct = 0.01 if tight_armed else 0.03
        stop_price = peak * (1 - trail_pct)
        if low_i <= stop_price:
            stopped_out = True
            signals.append(0)
        else:
            signals.append(1)
        # Update peak/tight_armed AFTER this bar's own stop check, using
        # this bar's own high -- a bar's own high must never retroactively
        # trip that same bar's own low against an inflated stop.
        if high_i > peak:
            peak = high_i
        if not tight_armed and peak >= entry_price * 1.20:
            tight_armed = True
    return signals
'''


def ipo_play_code(ipo_date: str) -> str:
    """Strategy source for one IPO play whose first trading day is `ipo_date` (YYYY-MM-DD)."""
    return _TEMPLATE.replace("__ENTRY__", ipo_date)
