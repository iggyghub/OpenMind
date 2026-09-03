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

IPO_POP_FADE_STRATEGY_CODE = '''def strategy(data) -> list:
    signals = []
    entry_price = data["Open"].iloc[0]
    peak = entry_price
    tight_armed = False
    stopped_out = False
    for i in range(len(data)):
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
