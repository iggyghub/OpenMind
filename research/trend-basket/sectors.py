"""Sector rotation, pre-registered standard variants only (no tuning). 9 original SPDR sector
ETFs (1998+, no survivorship problem). Monthly rebalance at month-end close, 2 bps/side on turnover."""
import numpy as np
import pandas as pd
import yfinance as yf

SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
px = yf.download(SECTORS + ["SPY"], start="1999-01-01", auto_adjust=True, progress=False)["Close"].dropna()
m = px.resample("ME").last()
mret = m.pct_change()
COST = 0.0002

mom6 = m[SECTORS].pct_change(6)
mom12_1 = m[SECTORS].shift(1).pct_change(11)
trend_ok = m["SPY"] > m["SPY"].rolling(10).mean()


def top3(score):
    w = pd.DataFrame(0.0, index=m.index, columns=SECTORS)
    for d in m.index:
        s = score.loc[d].dropna()
        if len(s) == len(SECTORS):
            w.loc[d, s.nlargest(3).index] = 1 / 3
    return w


def run(w):  # weights decided at month-end d, earn month d+1
    w = w.shift(1).fillna(0)
    r = (w * mret[w.columns]).sum(axis=1) - w.diff().abs().sum(axis=1).fillna(0) * COST
    return r


spy_w = pd.DataFrame({"SPY": 1.0}, index=m.index)
strategies = {
    "SPY buy & hold": mret["SPY"],
    "1. top 3 by 6-mo return": run(top3(mom6)),
    "2. top 3 by 12-1 mo return": run(top3(mom12_1)),
    "3. rule 1 + SPY 10-mo trend filter": run(top3(mom6).mul(trend_ok, axis=0)),
    "4. SPY + trend filter only": run(spy_w.mul(trend_ok, axis=0)),
}


def stats(r, start, end):
    r = r[(r.index >= start) & (r.index < end)].dropna()
    e = (1 + r).cumprod()
    yrs = len(r) / 12
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(12)


START = m.index[13]  # first month every variant has a full signal
for a, b in [(START, "2013-01-01"), ("2013-01-01", "2026-09-01"), (START, "2026-09-01")]:
    print(f"\n=== {pd.Timestamp(a).year}..{pd.Timestamp(b).year} ===")
    for name, r in strategies.items():
        c, d, s = stats(r, a, b)
        print(f"{name:36s} CAGR {c:+6.1%}  maxDD {d:5.0%}  Sharpe {s:4.2f}")
