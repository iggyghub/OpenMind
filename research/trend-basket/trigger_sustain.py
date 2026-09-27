"""Trigger x sustain grid. Regime turns ON when breadth crosses above TRIGGER; stays ON while
breadth > SUSTAIN; while ON, free slots are refilled daily. sustain=None = today's live design
(buy only on the trigger day). Same sim as stop_rule.py (12% trail, 20d cap, no 5% backstop,
close fills, prior-close signal + picks)."""
import sys
sys.path.insert(0, r"C:\Users\iggy\AppData\Local\Temp\claude\C--OpenMind\5cb6d305-c85c-4ead-ac51-0bd71200d9ec\scratchpad")
import numpy as np
import pandas as pd
from trend_basket_harness import Universe, COST, TRAIL, MAX_HOLD, N_SLOTS

u = Universe()
px = u.price[u.symbols]
score = u.mom20 * u.returns.rolling(20).std()
b = u.breadth.fillna(0)
days = px.index


def buy_days(trigger, sustain):
    out, on, prev = [], False, 0.0
    for v in b:
        crossed = v > trigger and prev <= trigger
        if sustain is None:
            out.append(crossed)
        else:
            on = crossed or (on and v > sustain)
            out.append(on)
        prev = v
    return pd.Series(out, index=b.index)


def simulate(signal, start, end):
    sig = signal.shift(1, fill_value=False)
    sc = score.shift(1)
    cash, pos, eq, invested = 1.0, {}, [], []
    for i, d in enumerate(days):
        if d < pd.Timestamp(start) or d >= pd.Timestamp(end):
            continue
        row = px.iloc[i]
        for sym in list(pos):
            p, c = pos[sym], row[sym]
            if pd.isna(c):
                continue
            if c <= p["peak"] * (1 - TRAIL) or i - p["i"] >= MAX_HOLD:
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
                pos[sym] = {"i": i, "peak": row[sym], "units": alloc * (1 - COST) / row[sym]}
        equity = cash + sum(p["units"] * row[s] for s, p in pos.items() if pd.notna(row[s]))
        eq.append((d, equity))
        invested.append(1 - cash / equity)
    e = pd.Series(dict(eq))
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252), np.mean(invested)


GRID = [(60, None), (55, None), (60, 60), (60, 55), (60, 50), (55, 55), (55, 50), (0, 0)]
PERIODS = [("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2005-06-01", "2026-09-01")]
for start, end in PERIODS:
    print(f"\n=== {start[:4]}..{end[:4]} ===")
    print(f"{'trigger / sustain':28s} {'CAGR':>7s} {'maxDD':>6s} {'Sharpe':>6s} {'invested':>8s}")
    for t, s in GRID:
        label = ("no gate (always refill)" if t == 0 else
                 f"cross {t}%, buy that day only" if s is None else f"cross {t}%, refill while >{s}%")
        c, d, sh, inv = simulate(buy_days(t / 100, None if s is None else s / 100), start, end)
        print(f"{label:28s} {c:+7.1%} {d:6.0%} {sh:6.2f} {inv:8.0%}")
