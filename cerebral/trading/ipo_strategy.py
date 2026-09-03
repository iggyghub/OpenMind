"""Hand-written (not LLM-generated) strategy code for IPO pop-then-fade plays.

Not routed through to_strategy -- see cerebral/trading/discovery.py's IPO-calendar dispatch
path, which registers this code directly via StrategyStore.save(), bypassing _run_gauntlet's
per-symbol backtest (a brand-new IPO ticker has no price history to backtest against before
its first trading day -- see CONTEXT.md's "IPO play" glossary entry for the full reasoning).
This module's own code was validated once via a real _run_gauntlet call against historical
IPO tickers before being adopted -- not on every new ticker it gets applied to.
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
        high_i = data["High"].iloc[i]
        low_i = data["Low"].iloc[i]
        if high_i > peak:
            peak = high_i
        if not tight_armed and peak >= entry_price * 1.20:
            tight_armed = True
        trail_pct = 0.01 if tight_armed else 0.03
        stop_price = peak * (1 - trail_pct)
        if low_i <= stop_price:
            stopped_out = True
            signals.append(0)
        else:
            signals.append(1)
    return signals
'''
