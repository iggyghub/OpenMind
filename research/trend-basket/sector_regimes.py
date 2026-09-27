"""Q1: how long do sector bull/bear phases last, which horizon's leadership persists, and is there
real calendar seasonality? 9 original SPDR sector ETFs, 1999-2026."""
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import spearmanr

S9 = {"XLB": "Materials", "XLE": "Energy", "XLF": "Financials", "XLI": "Industrials", "XLK": "Tech",
      "XLP": "Staples", "XLU": "Utilities", "XLV": "Health care", "XLY": "Discretionary"}
px = yf.download(list(S9), start="1999-01-01", auto_adjust=True, progress=False)["Close"].dropna()
yrs = (px.index[-1] - px.index[0]).days / 365.25


def runs(flag):
    """Lengths (trading days) of consecutive True and False runs."""
    change = flag.ne(flag.shift()).cumsum()
    g = flag.groupby(change)
    return pd.DataFrame({"state": g.first(), "len": g.size()})


print("=== 1a. Phase lengths: price vs its 200-day average ===")
print(f"{'sector':14s} {'switches/yr':>11s} {'median bull (days)':>18s} {'median bear':>11s} {'% time bull':>11s}")
for t, name in S9.items():
    above = (px[t] > px[t].rolling(200).mean()).iloc[200:]
    r = runs(above)
    print(f"{name:14s} {len(r) / yrs:11.1f} {r[r.state]['len'].median():18.0f} {r[~r.state]['len'].median():11.0f} {above.mean():11.0%}")
print("(many switches = the line gets crossed back and forth; the medians show typical phase length)")

print("\n=== 1b. Classic bull/bear: a 20% drop from a peak starts a bear, a 20% rise from a trough a bull ===")
for t, name in S9.items():
    s, state, ext, phases, start = px[t], "bull", px[t].iloc[0], [], px.index[0]
    for d, v in s.items():
        if state == "bull":
            ext = max(ext, v)
            if v <= ext * 0.8:
                phases.append(("bull", start, d)); state, ext, start = "bear", v, d
        else:
            ext = min(ext, v)
            if v >= ext * 1.2:
                phases.append(("bear", start, d)); state, ext, start = "bull", v, d
    bears = [(b - a).days / 30.4 for k, a, b in phases if k == "bear"]
    bulls = [(b - a).days / 30.4 for k, a, b in phases if k == "bull"]
    print(f"{name:14s} bears: {len(bears):2d} (median {np.median(bears) if bears else 0:4.1f} mo) | "
          f"bulls: {len(bulls):2d} (median {np.median(bulls) if bulls else 0:5.1f} mo)")

print("\n=== 1c. Does sector leadership persist? rank correlation of consecutive periods ===")
print("(positive = last period's leaders keep leading; negative = they reverse; t > 2 is meaningful)")
for label, n in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("6 months", 126), ("12 months", 252)):
    cuts = px.iloc[::n]
    r = cuts.pct_change().dropna()
    rho = [spearmanr(r.iloc[i], r.iloc[i + 1])[0] for i in range(len(r) - 1)]
    rho = pd.Series(rho)
    print(f"{label:10s} mean rho {rho.mean():+.3f}  t={rho.mean() / rho.std() * np.sqrt(len(rho)):+.1f}  (n={len(rho)} periods)")

print("\n=== 1d. Seasonality: does a sector's best/worst calendar month repeat out of sample? ===")
m = px.resample("ME").last().pct_change().dropna()
m = m.sub(m.mean(axis=1), axis=0)  # relative to the average sector that month
first, second = m[m.index < "2013-01-01"], m[m.index >= "2013-01-01"]
f = first.groupby(first.index.month).mean()
s = second.groupby(second.index.month).mean()
rho, p = spearmanr(f.to_numpy().ravel(), s.to_numpy().ravel())
print(f"sector x month averages, 1999-2012 vs 2013-2026: rank correlation {rho:+.2f} (p={p:.2f}, 108 cells)")
best = f.stack().sort_values(ascending=False)
print("5 strongest sector-months in 1999-2012, and what they did in 2013-2026 (vs average sector):")
for (mon, t), v in best.head(5).items():
    print(f"  {S9[t]:13s} month {mon:2d}: {v:+.1%} then {s.loc[mon, t]:+.1%}")
