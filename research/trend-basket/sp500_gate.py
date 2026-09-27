"""S&P 500 point-in-time breadth as the GATE, at several trigger/sustain levels, used two ways:
(a) hold SPY while the gate is on, cash otherwise; (b) the trend basket (picks from S&P members,
the closest backtestable proxy -- live picks come from daily movers, which have no history)."""
import numpy as np
import pandas as pd

exec(open("sp500_backtest.py", encoding="utf-8").read().split("VARIANTS = {")[0])  # data, breadth, signal(), simulate()

spy_ret = spy.pct_change().fillna(0)


def spy_gated(sig, start, end):
    on = sig.shift(1, fill_value=False)  # decided on the prior close
    m = (spy.index >= start) & (spy.index < end)
    r = (spy_ret * on.astype(float))[m]
    r = r - on.astype(float).diff().abs().fillna(0)[m] * COST  # cost on each switch
    e = (1 + r).cumprod()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252), on[m].mean()


print(f"S&P breadth percentiles 2005+: " + ", ".join(f"p{q}={breadth['2005':].quantile(q / 100):.0%}" for q in (10, 25, 50, 75, 90)))
LEVELS = [(0.55, 0.50), (0.60, 0.55), (0.65, 0.60), (0.70, 0.60), (0.70, 0.65)]
for start, end in [("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2005-06-01", "2026-09-01")]:
    s = spy[(spy.index >= start) & (spy.index < end)]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    sr = s.pct_change().dropna()
    print(f"\n=== {start[:4]}..{end[:4]} ===   SPY buy&hold: CAGR {(s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1:+.1%}  maxDD {(s / s.cummax() - 1).min():.0%}  Sharpe {sr.mean() / sr.std() * np.sqrt(252):.2f}")
    print(f"{'gate (cross / stay above)':26s} {'SPY-gated CAGR/DD/Sharpe/on':32s} {'basket CAGR/DD/Sharpe'}")
    for t, su in LEVELS:
        sig = signal(t, su)
        a = spy_gated(sig, start, end)
        b = simulate(sig, start, end)
        print(f"{f'{t:.0%} / {su:.0%}':26s} {a[0]:+6.1%} {a[1]:5.0%} {a[2]:5.2f} {a[3]:4.0%}     {b[0]:+6.1%} {b[1]:5.0%} {b[2]:5.2f}")
