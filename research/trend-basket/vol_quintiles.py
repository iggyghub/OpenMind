"""Do more volatile stocks get you up faster? Point-in-time S&P 500 members, each month-end sorted
into 5 groups by trailing 60-day volatility; equal-weight hold for the next month."""
import pickle

import numpy as np
import pandas as pd

DATA = "C:/OpenMind/cerebral/data/"
px = pd.DataFrame(pickle.load(open(DATA + "research_sp500_close.pkl", "rb"))).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()]
hist = pd.read_csv(DATA + "research_sp500_hist.csv", parse_dates=["date"]).sort_values("date")
vol = px.pct_change(fill_method=None).rolling(60).std() * np.sqrt(252)
me = px.resample("ME").last().index
ends = [px.index[px.index <= d][-1] for d in me if (px.index <= d).any()]
out = []
for a, b in zip(ends[:-1], ends[1:]):
    row = hist[hist.date <= a]
    if row.empty:
        continue
    mem = [s for s in row.iloc[-1]["tickers"].split(",") if s in px.columns]
    v = vol.loc[a, mem].dropna()
    r = (px.loc[b, v.index] / px.loc[a, v.index] - 1)
    ok = r.notna()
    v, r = v[ok], r[ok]
    if len(v) < 100:
        continue
    q = pd.qcut(v.rank(method="first"), 5, labels=False)
    out.append(pd.Series([r[q == i].mean() for i in range(5)], name=b))
m = pd.DataFrame(out)
yrs = len(m) / 12
print(f"{m.index[0].date()} .. {m.index[-1].date()}, {len(m)} months")
print(f"{'group':18s} {'avg month':>9s} {'grew/yr':>8s} {'swing/yr':>8s} {'worst drop':>10s} {'up months':>9s}")
for i, lab in enumerate(["1 calmest", "2", "3", "4", "5 most volatile"]):
    s = m[i]
    e = (1 + s).cumprod()
    print(f"{lab:18s} {s.mean():+9.2%} {e.iloc[-1] ** (1 / yrs) - 1:+8.1%} {s.std() * np.sqrt(12):8.0%} "
          f"{(e / e.cummax() - 1).min():10.0%} {(s > 0).mean():9.0%}")

print("\ngrowth per year by period (calmest .. most volatile):")
for a, b in (("2004", "2008"), ("2009", "2012"), ("2013", "2019"), ("2020", "2022"), ("2023", "2026")):
    p = m.loc[a:b]
    g = (1 + p).prod() ** (12 / len(p)) - 1
    print(f"  {a}-{b}: " + "  ".join(f"{x:+6.1%}" for x in g))
