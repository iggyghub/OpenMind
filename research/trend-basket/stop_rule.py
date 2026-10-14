"""12% trail (design) vs + live_tick's 5% stop / 30% take-profit backstop. Same sim as gate_variants."""
import sys
sys.path.insert(0, r"C:\Users\iggy\AppData\Local\Temp\claude\C--OpenMind\5cb6d305-c85c-4ead-ac51-0bd71200d9ec\scratchpad")
import numpy as np
import pandas as pd
from trend_basket_harness import Universe, COST, TRAIL, MAX_HOLD, N_SLOTS

u = Universe()
px = u.price[u.symbols]
score = u.mom20 * u.returns.rolling(20).std()
b = u.breadth
days = px.index
on = (b > 0.60).fillna(False)
signal = on & ~on.shift(1, fill_value=False)


def simulate(start, end, backstop):
    sig = signal.shift(1, fill_value=False)
    sc = score.shift(1)
    cash, pos, eq = 1.0, {}, []
    for i, d in enumerate(days):
        if d < pd.Timestamp(start) or d >= pd.Timestamp(end):
            continue
        row = px.iloc[i]
        for sym in list(pos):
            p, c = pos[sym], row[sym]
            if pd.isna(c):
                continue
            exit_now = (c <= p["peak"] * (1 - TRAIL) or i - p["i"] >= MAX_HOLD
                        or (backstop and (c <= p["entry"] * 0.95 or c >= p["entry"] * 1.30)))
            if exit_now:
                cash += p["units"] * c * (1 - COST)
                pos.pop(sym)
            else:
                p["peak"] = max(p["peak"], c)
        equity = cash + sum(p["units"] * row[s] for s, p in pos.items() if pd.notna(row[s]))
        if sig.iloc[i] and len(pos) < N_SLOTS:
            for sym in sc.iloc[i].dropna().sort_values(ascending=False).index:
                if len(pos) >= N_SLOTS:
                    break
                if sym in pos or pd.isna(row[sym]):
                    continue
                alloc = min(equity * 0.10, cash)
                if alloc <= 1e-9:
                    break
                cash -= alloc
                pos[sym] = {"i": i, "entry": row[sym], "peak": row[sym], "units": alloc * (1 - COST) / row[sym]}
        eq.append((d, cash + sum(p["units"] * row[s] for s, p in pos.items() if pd.notna(row[s]))))
    e = pd.Series(dict(eq))
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252)


for start, end in [("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2005-06-01", "2026-09-01")]:
    print(f"\n=== {start[:4]}..{end[:4]} ===")
    for label, bs in (("12% trail only (design)", False), ("+ 5% stop / 30% take-profit (current)", True)):
        c, d, s = simulate(start, end, bs)
        print(f"{label:40s} CAGR {c:+6.1%}  maxDD {d:5.0%}  Sharpe {s:4.2f}")
