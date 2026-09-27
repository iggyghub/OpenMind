"""Trend basket with SECTOR filters (pre-registered, nothing else run):
A. picks only from sectors whose own breadth (% of its S&P members above 50d MA) > 60%
B. picks only from the top 3 sectors by 12-1 month return (equal-weight sector index), monthly
Same mechanics as sp500_backtest.py (10 slots x 10%, 12% trail, 20d cap, prior-close signals)."""
import pickle
import numpy as np
import pandas as pd

exec(open("sp500_backtest.py", encoding="utf-8").read().split("VARIANTS = {")[0])
sec = pickle.load(open("sector_map.pkl", "rb"))
cols = [c for c in px.columns if sec.get(c)]
sectors = sorted({sec[c] for c in cols})
print("sectors:", sectors)

# A: per-sector breadth
above = (px > ma50) & valid
elig_a = pd.DataFrame(False, index=px.index, columns=px.columns)
for s in sectors:
    sc = [c for c in cols if sec[c] == s]
    b = above[sc].sum(axis=1) / valid[sc].sum(axis=1).replace(0, np.nan)
    elig_a[sc] = np.repeat((b > 0.60).to_numpy()[:, None], len(sc), axis=1)

# B: top 3 sectors by 12-1 month return of an equal-weight sector index (members only)
daily = ret.where(members)
sec_idx = pd.DataFrame({s: (1 + daily[[c for c in cols if sec[c] == s]].mean(axis=1).fillna(0)).cumprod() for s in sectors})
mo = sec_idx.resample("ME").last()
mom = mo.shift(1).pct_change(11)
top3 = mom.apply(lambda r: set(r.dropna().nlargest(3).index) if r.notna().sum() >= 3 else set(), axis=1)
top3_daily = top3.reindex(px.index, method="ffill").shift(1)  # decided at the prior month-end
elig_b = pd.DataFrame({c: top3_daily.apply(lambda s, c=c: isinstance(s, set) and sec[c] in s) for c in cols}).reindex(columns=px.columns, fill_value=False)

base_score = score.copy()
always = pd.Series(True, index=breadth.index)
for start, end in [("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2005-06-01", "2026-09-01")]:
    s = spy[(spy.index >= start) & (spy.index < end)]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    sr = s.pct_change().dropna()
    print(f"\n=== {start[:4]}..{end[:4]} ===")
    print(f"{'SPY buy & hold':40s} CAGR {(s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1:+6.1%}  maxDD {(s / s.cummax() - 1).min():5.0%}  Sharpe {sr.mean() / sr.std() * np.sqrt(252):4.2f}")
    for name, elig in [("basket, all S&P stocks (baseline)", None), ("A. only sectors with breadth > 60%", elig_a),
                       ("B. only top-3 momentum sectors", elig_b)]:
        score = base_score if elig is None else base_score.where(elig)
        c, d, sh, inv = simulate(always, start, end)
        print(f"{name:40s} CAGR {c:+6.1%}  maxDD {d:5.0%}  Sharpe {sh:4.2f}  invested {inv:4.0%}")
    score = base_score
