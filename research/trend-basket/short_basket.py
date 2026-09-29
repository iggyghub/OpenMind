"""Short basket, the trend basket flipped for down-trends. As pre-registered in SHORT-BASKET-PLAN.md."""
import pathlib

import numpy as np
import pandas as pd

# ponytail: reuse sp500_backtest.py's data prep (prices, membership, breadth, score) by running its top half
src = pathlib.Path(__file__).with_name("sp500_backtest.py").read_text(encoding="utf-8")
exec(src.split("def signal")[0])
BORROW = 0.005 / 252


def short_signal(trigger=0.45, sustain=0.50):
    out, on, prev = [], False, 1.0
    for v in breadth.fillna(1):
        crossed = v < trigger and prev >= trigger
        on = crossed or (on and v < sustain)
        out.append(on)
        prev = v
    return pd.Series(out, index=breadth.index)


def simulate_short(sig, start, end, trail=TRAIL, max_hold=MAX_HOLD):
    sig, sc = sig.shift(1, fill_value=False), score.shift(1)
    cash, pos, eq, trades = 1.0, {}, [], []
    for i, d in enumerate(px.index):
        if d < pd.Timestamp(start) or d >= pd.Timestamp(end):
            continue
        row = px.iloc[i]
        for sym in list(pos):
            p, c = pos[sym], row[sym]
            if pd.isna(c):
                p["gap"] += 1
                c = p["last"]
                if p["gap"] <= 5:
                    continue  # prices stopped for 5+ days (delisted/acquired): cover at the last price
            else:
                p["gap"], p["last"] = 0, c
                p["low"] = min(p["low"], c)
                if not (c >= p["low"] * (1 + trail) or i - p["i"] >= max_hold):
                    continue
            held = i - p["i"]
            pnl = p["size"] * (p["entry"] / c - 1) - p["size"] * (COST * 2 + BORROW * held)
            cash += p["size"] + pnl
            trades.append((pnl / p["size"], d))
            pos.pop(sym)
        equity = cash + sum(p["size"] * (p["entry"] / p["last"] - 1) + p["size"] for p in pos.values())
        if sig.iloc[i] and len(pos) < N_SLOTS:
            for sym in sc.iloc[i].dropna().sort_values().index:  # lowest score first
                if len(pos) >= N_SLOTS:
                    break
                if sym in pos or pd.isna(row[sym]):
                    continue
                size = min(equity * 0.10, cash)
                if size <= 1e-9:
                    break
                cash -= size  # ponytail: collateral set aside per short, no margin leverage
                pos[sym] = {"i": i, "entry": row[sym], "last": row[sym], "low": row[sym], "gap": 0, "size": size}
        eq.append((d, cash + sum(p["size"] * (p["entry"] / p["last"] - 1) + p["size"] for p in pos.values())))
    e = pd.Series(dict(eq))
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), pd.DataFrame(trades, columns=["ret", "date"])


# ---- run ----
sig = short_signal()
print(f"short regime on {sig['2005-06-01':].mean():.0%} of days")
ok = True
for start, end in [("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2005-06-01", "2026-09-01")]:
    cagr, dd, t = simulate_short(sig, start, end)
    avg = t.ret.mean()
    tt = avg / t.ret.std() * np.sqrt(len(t))
    print(f"{start[:4]}..{end[:4]}: {len(t)} short bets, avg {avg:+.2%} (t={tt:+.1f}), winners {np.mean(t.ret > 0):.0%}, "
          f"middle 80% {t.ret.quantile(.1):+.1%}..{t.ret.quantile(.9):+.1%} | account {cagr:+.1%}/yr, worst drop {dd:.0%}")
    if end != "2026-09-01" or start != "2005-06-01":
        ok &= avg > 0
    if start == "2005-06-01" and end == "2026-09-01":
        by = t.groupby(t.date.dt.year).ret.agg(["size", "mean"])
        print("  by year (bets, avg): " + "  ".join(f"{y % 100}:{int(n)}/{m:+.1%}" for y, (n, m) in by.iterrows()))
print("PASS" if ok else "no edge (fails the pre-registered bar)")
