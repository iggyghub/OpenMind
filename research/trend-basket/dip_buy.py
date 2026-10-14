"""Buy the dip: how deep do sector dips go, and is being down X% a good time to buy?
Exactly as pre-registered in DIP-BUY-PLAN.md (2026-09-27)."""
import numpy as np
import pandas as pd
import yfinance as yf

S9 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
COST = 0.0002
px = yf.download(S9 + ["SPY"], start="1998-12-01", auto_adjust=True, progress=False)["Close"].dropna()

print("=== 1. How deep do sector dips go? (episodes reaching 10% below the all-time high) ===")
depths = []
for t in S9:
    s = px[t]
    dd = s / s.cummax() - 1
    episode = (dd < 0).ne((dd < 0).shift()).cumsum()[dd < 0]
    for _, g in dd[dd < 0].groupby(episode):
        if g.min() <= -0.10:
            depths.append(-g.min())
d = pd.Series(depths)
print(f"{len(d)} dips of 10%+ across 9 sectors. Final depth: median {d.median():.0%}, "
      f"quartiles {d.quantile(.25):.0%}..{d.quantile(.75):.0%}")
print(f"of those 10% dips: {np.mean(d >= .2):.0%} went to 20%+, {np.mean(d >= .3):.0%} to 30%+, "
      f"{np.mean(d >= .5):.0%} to 50%+")

print("\n=== 2. Forward return by how far a sector is below its 12-month high (month-ends) ===")
m = px.resample("ME").last()
dd12 = (m / px.rolling(252).max().resample("ME").last() - 1)[S9].iloc[12:]
BUCKETS = [(0, .05), (.05, .10), (.10, .15), (.15, .20), (.20, .30), (.30, 1.0)]
for h in (3, 6, 12):
    fwd = (m.shift(-h) / m - 1)[S9].reindex(dd12.index)
    rel = fwd - (fwd.sum(axis=1).to_numpy()[:, None] - fwd) / 8  # vs the other 8 funds
    print(f"-- next {h} months (raw / vs other sectors; t overlap-adjusted) --")
    for lo, hi in BUCKETS:
        mask = (-dd12 >= lo) & (-dd12 < hi)
        x, xr = rel[mask].stack(), fwd[mask].stack()
        if len(x) < 5:
            continue
        tt = x.mean() / x.std() * np.sqrt(len(x) / h)
        halves = [x[x.index.get_level_values(0) < "2013-01-01"].mean(), x[x.index.get_level_values(0) >= "2013-01-01"].mean()]
        print(f"  down {lo:4.0%}-{hi:4.0%}: n={len(x):4d}  raw {xr.mean():+6.1%}  vs others {x.mean():+6.1%}  "
              f"t={tt:+.1f}  (99-12 {halves[0]:+.1%}, 13-26 {halves[1]:+.1%})")

print("\n=== 3. Rule: hold only the sectors down >= X% from their 12-month high (else all 9) ===")
r = m.pct_change().shift(-1).reindex(dd12.index)
sig = dd12.index[dd12.index <= "2026-08-31"]
ew = r.loc[sig, S9].mean(axis=1)
spy = r.loc[sig, "SPY"]


def st(x):
    e = (1 + x).cumprod()
    return e.iloc[-1] ** (12 / len(x)) - 1, (e / e.cummax() - 1).min(), x.mean() / x.std() * np.sqrt(12)


HALVES = (("2000-2012", "1999-12-31", "2012-11-30"), ("2013-2026", "2012-12-31", "2026-08-31"))
for X in (.10, .15, .20):
    pick = (-dd12.loc[sig] >= X)
    w = pick.div(pick.sum(axis=1), axis=0)
    w[pick.sum(axis=1) == 0] = 1 / 9
    rule = (w * r.loc[sig, S9]).sum(axis=1) - w.diff().abs().sum(axis=1).fillna(1) * COST
    b = pd.DataFrame({f"dip {X:.0%}": rule, "EW hold": ew, "SPY": spy})
    print(f"-- X = {X:.0%}: a dip is on {(pick.sum(axis=1) > 0).mean():.0%} of months --")
    ok = True
    for lab, a, z in HALVES + (("all", "1999-12-31", "2026-08-31"),):
        part = b.loc[a:z]
        s = {k: st(part[k]) for k in part}
        print(f"  {lab}: " + " | ".join(f"{k} {c:+.1%}/yr DD {dd:.0%} Sh {sh:.2f}" for k, (c, dd, sh) in s.items()))
        if lab != "all":
            ok &= s[f"dip {X:.0%}"][0] > max(s["EW hold"][0], s["SPY"][0])
    print("  PASS" if ok else "  no edge (fails the pre-registered bar)")
