"""Sector rotation design questions (robustness, not optimisation). Signal locked: 12-1 month
return (t-252 .. t-21 trading days). Daily data; weights set at the close of a rebalance day earn
from the next day. 2 bps/side on turnover. 9 original SPDR sectors unless noted."""
import numpy as np
import pandas as pd
import yfinance as yf

S9 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
S11 = S9 + ["XLRE", "XLC"]
px = yf.download(S11 + ["SPY", "BIL"], start="1999-01-01", auto_adjust=True, progress=False)["Close"]
ret = px.pct_change(fill_method=None)
mom = px.shift(21) / px.shift(252) - 1
COST = 0.0002


def run(sectors, n_hold=3, rebalance="ME", offset=0, absolute=False, start="2000-01-01"):
    p = px.loc[start:, sectors].dropna()
    days = p.index
    if rebalance == "W":
        rdays = set(days[::5])
    elif rebalance == "2W":
        rdays = set(days[::10])
    else:
        ends = pd.Series(days, index=days).groupby(days.to_period(rebalance[0])).last()
        idx = [min(days.get_loc(d) + offset, len(days) - 1) for d in ends]
        rdays = set(days[idx])
    w = pd.DataFrame(0.0, index=days, columns=sectors)
    cur = pd.Series(0.0, index=sectors)
    for d in days:
        if d in rdays:
            m = mom.loc[d, sectors].dropna()
            cur = pd.Series(0.0, index=sectors)
            if len(m) == len(sectors):
                top = m.nlargest(n_hold)
                if absolute:
                    top = top[top > 0]  # a weak sector's slot goes to cash
                cur[top.index] = 1 / n_hold
        w.loc[d] = cur
    w = w.shift(1).fillna(0)
    r = (w * ret.loc[days, sectors]).sum(axis=1) - w.diff().abs().sum(axis=1).fillna(0) * COST
    return r


def stats(r, a, b):
    r = r[(r.index >= a) & (r.index < b)].dropna()
    e = (1 + r).cumprod()
    return e.iloc[-1] ** (252 / len(r)) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252)


HALVES = [("2000-01-01", "2013-01-01"), ("2013-01-01", "2026-09-01"), ("2000-01-01", "2026-09-01")]


def show(label, r):
    cells = []
    for a, b in HALVES:
        c, d, s = stats(r, a, b)
        cells.append(f"{c:+6.1%} {d:4.0%} {s:4.2f}")
    print(f"{label:34s} " + " | ".join(cells))


print(f"{'':34s} {'2000-2012 CAGR/DD/Sh':18s} | {'2013-2026':18s} | {'2000-2026':18s}")
show("SPY buy & hold", ret["SPY"])
show("equal weight all 9 sectors", ret[S9].mean(axis=1))
print("-- Q2: how many to hold (monthly) --")
for n in (2, 3, 4):
    show(f"top {n}", run(S9, n_hold=n))
print("-- Q3a: how often to swap (top 3) --")
for rb, lab in (("W", "weekly"), ("2W", "every 2 weeks"), ("ME", "monthly"), ("QE", "quarterly")):
    show(lab, run(S9, rebalance=rb))
print("-- Q3b: which day of the month (top 3, monthly) --")
for off, lab in ((0, "month-end"), (5, "+5 trading days"), (10, "+10 (mid-month)"), (15, "+15 trading days")):
    show(lab, run(S9, offset=off))
print("-- Q3c: sit out weak sectors (top 3, monthly) --")
show("absolute momentum filter", run(S9, absolute=True))
print("-- Q1: all 11 sectors vs 9, 2019+ only --")
for name, r in (("SPY", ret["SPY"]), ("top 3 of 9", run(S9, start="2019-01-01")), ("top 3 of 11", run(S11, start="2019-01-01"))):
    c, d, s = stats(r, "2019-01-01", "2026-09-01")
    print(f"{name:34s} 2019-2026: {c:+6.1%} {d:4.0%} {s:4.2f}")
