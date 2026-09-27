"""Trend basket on the S&P 500 AS IT WAS each day (point-in-time membership, including later-
removed companies where free price data exists). Same mechanics as trigger_sustain.py: 10 slots
x 10%, 12% trail, 20-day cap, close fills, 2 bps/side, signal + picks from the prior close."""
import pickle
import numpy as np
import pandas as pd

COST, TRAIL, MAX_HOLD, N_SLOTS = 0.0002, 0.12, 20, 10

closes = pickle.load(open("C:/OpenMind/cerebral/data/research_sp500_close.pkl", "rb"))
px = pd.DataFrame(closes).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()]
spy = __import__("yfinance").download("SPY", start="2004-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
px = px.reindex(spy.index)

# point-in-time membership
hist = pd.read_csv("C:/OpenMind/cerebral/data/research_sp500_hist.csv", parse_dates=["date"]).sort_values("date")
rows = {d: set(t.split(",")) for d, t in zip(hist["date"], hist["tickers"])}
change_days = sorted(rows)
members = pd.DataFrame(False, index=px.index, columns=px.columns)
for i, d in enumerate(change_days):
    end = change_days[i + 1] if i + 1 < len(change_days) else px.index[-1] + pd.Timedelta(days=1)
    cols = [c for c in rows[d] if c in members.columns]
    members.loc[(members.index >= d) & (members.index < end), cols] = True

# coverage: share of each day's members that have a price that day
n_members = pd.Series({d: len(rows[max(c for c in change_days if c <= d)]) for d in px.index[::21]})
n_priced = (members & px.notna()).sum(axis=1)[px.index[::21]]
cov = (n_priced / n_members).groupby(n_priced.index.year).mean()
print("coverage by year (members with price data):")
print("  " + "  ".join(f"{y}:{v:.0%}" for y, v in cov.items() if 2005 <= y <= 2026))

ret = px.pct_change(fill_method=None)
ma50 = px.rolling(50, min_periods=50).mean()
valid = members & ma50.notna() & px.notna()
breadth = ((px > ma50) & valid).sum(axis=1) / valid.sum(axis=1)
score = (px.pct_change(20, fill_method=None) * ret.rolling(20).std()).where(members)
print(f"S&P breadth median {breadth['2005':].median():.0%}")


def signal(trigger, sustain):
    out, on, prev = [], False, 0.0
    for v in breadth.fillna(0):
        crossed = v > trigger and prev <= trigger
        on = crossed if sustain is None else (crossed or (on and v > sustain))
        out.append(on)
        prev = v
    return pd.Series(out, index=breadth.index)


TRADES = []  # (return, days held, exit reason) of every closed bet in the last simulate() call


def simulate(sig, start, end):
    TRADES.clear()
    sig = sig.shift(1, fill_value=False)
    sc = score.shift(1)
    cash, pos, eq, inv = 1.0, {}, [], []
    days = px.index
    for i, d in enumerate(days):
        if d < pd.Timestamp(start) or d >= pd.Timestamp(end):
            continue
        row = px.iloc[i]
        for sym in list(pos):
            p, c = pos[sym], row[sym]
            if pd.isna(c):
                p["gap"] += 1
                if p["gap"] > 5:  # prices stopped (delisted/acquired): out at the last price
                    cash += p["units"] * p["last"] * (1 - COST)
                    TRADES.append((p["units"] * p["last"] * (1 - COST) / p["cost"] - 1, i - p["i"], "delisted"))
                    pos.pop(sym)
                continue
            p["gap"], p["last"] = 0, c
            if c <= p["peak"] * (1 - TRAIL) or i - p["i"] >= MAX_HOLD:
                cash += p["units"] * c * (1 - COST)
                TRADES.append((p["units"] * c * (1 - COST) / p["cost"] - 1, i - p["i"], "stop" if i - p["i"] < MAX_HOLD else "20-day"))
                pos.pop(sym)
            else:
                p["peak"] = max(p["peak"], c)
        equity = cash + sum(p["units"] * p["last"] for p in pos.values())
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
                pos[sym] = {"i": i, "peak": row[sym], "last": row[sym], "gap": 0, "cost": alloc, "units": alloc * (1 - COST) / row[sym]}
        equity = cash + sum(p["units"] * p["last"] for p in pos.values())
        eq.append((d, equity))
        inv.append(1 - cash / equity)
    e = pd.Series(dict(eq))
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252), np.mean(inv)


VARIANTS = {
    "cross 55%, refill while >50% (LIVE)": signal(0.55, 0.50),
    "cross 60%, buy that day only (old)": signal(0.60, None),
    "no gate (always refill)": pd.Series(True, index=breadth.index),
}
for start, end in [("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2005-06-01", "2026-09-01")]:
    s = spy[(spy.index >= start) & (spy.index < end)]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    sr = s.pct_change().dropna()
    print(f"\n=== {start[:4]}..{end[:4]} ===")
    print(f"{'SPY buy & hold':38s} CAGR {(s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1:+6.1%}  maxDD {(s / s.cummax() - 1).min():5.0%}  Sharpe {sr.mean() / sr.std() * np.sqrt(252):4.2f}")
    for name, sig in VARIANTS.items():
        c, d, sh, inv = simulate(sig, start, end)
        print(f"{name:38s} CAGR {c:+6.1%}  maxDD {d:5.0%}  Sharpe {sh:4.2f}  invested {inv:4.0%}")

# ---- per-bet reference for the live Trend Basket card (LIVE variant, full period) ----
simulate(VARIANTS["cross 55%, refill while >50% (LIVE)"], "2005-06-01", "2026-09-01")
t = pd.DataFrame(TRADES, columns=["ret", "days", "exit"])
print(f"\n=== per bet, LIVE variant 2005-2026: {len(t)} closed bets ===")
print(f"average {t.ret.mean():+.2%}  median {t.ret.median():+.2%}  positive {np.mean(t.ret > 0):.0%}  "
      f"middle 80% {t.ret.quantile(.1):+.1%}..{t.ret.quantile(.9):+.1%}  avg days held {t.days.mean():.1f}")
print("exits: " + ", ".join(f"{k} {v:.0%}" for k, v in t.exit.value_counts(normalize=True).items()))
for n in (10, 30):  # how much the average of the first n live bets can wander by luck alone
    m = [t.ret.sample(n, replace=True, random_state=k).mean() for k in range(2000)]
    print(f"average of {n} random bets: 80% of the time between {np.quantile(m, .1):+.2%} and {np.quantile(m, .9):+.2%}")
