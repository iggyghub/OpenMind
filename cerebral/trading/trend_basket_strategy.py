"""Hand-written (not LLM-generated) strategy code for the trend-basket play (ADR-0038).

Unlike the IPO play, this code IS routed through the normal `_run_gauntlet` per-symbol backtest
for every symbol it's dispatched to -- see CONTEXT.md's "Trend basket play" glossary entry and
docs/adr/0038-trend-basket-strategy.md for why no Gauntlet bypass applies here (every candidate
this strategy can select already has enough price history to backtest normally).

Exit is a flat, non-tightening 12% trailing stop from the peak since entry, hard-capped at bar 20
(entry is bar 0). Deliberately NOT the IPO play's tighten-to-1%-after-+20% ratchet -- that shape
was tested against ordinary multi-day swing holds and found to destroy this strategy's edge, at
both daily and 5-minute granularity (see the ADR's "Why the exit isn't the IPO play's exit").

Same stop-check-before-peak-update ordering ipo_strategy.py uses (fixed there 2026-09-03 after a
same-bar-inflated-peak bug): the stop check for bar i uses the peak as of the END of bar i-1, so a
bar's own high can never retroactively save that same bar's own low from a stop it should have
tripped.
"""

TREND_BASKET_STRATEGY_CODE = '''def strategy(data) -> list:
    signals = []
    entry_price = data["Open"].iloc[0]
    peak = entry_price
    stopped_out = False
    for i in range(len(data)):
        if stopped_out or i > 20:
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
