"""Same-day %-exit trades on the 10 most volatile of 30 stocks. As pre-registered in SAME-DAY-PLAN.md."""
import itertools
import pathlib
import sqlite3

import numpy as np
import pandas as pd

COST = 0.0002
LEVELS = (0.01, 0.02, 0.03, 0.05)
COMBOS = list(itertools.product(LEVELS, LEVELS))  # (take profit X, stop Y)

bars = pd.read_sql(
    "SELECT symbol, ts, open, high, low, close FROM bars WHERE interval='5m' "
    "AND substr(ts, 12, 5) BETWEEN '09:30' AND '15:55' ORDER BY symbol, ts",
    sqlite3.connect("C:/OpenMind/cerebral/data/bars.db"))
bars["day"] = bars.ts.str[:10]
print(f"{len(bars):,} regular-session bars, {bars.symbol.nunique()} stocks, {bars.day.nunique()} days")

# ---- per stock-day outcome for every (X, Y), plus the plain hold ----
out = {}
for (sym, day), g in bars.groupby(["symbol", "day"], sort=False):
    if g.ts.iloc[0][11:16] != "09:30" or g.ts.iloc[-1][11:16] != "15:55" or len(g) < 60:
        continue  # half days / gaps: skip
    entry = g.close.iloc[0]
    o, h, l = g.open.to_numpy()[1:], g.high.to_numpy()[1:], g.low.to_numpy()[1:]
    last = g.close.iloc[-1]
    res = {"hold": last / entry - 1, "close": last}
    for x, y in COMBOS:
        s, t = entry * (1 - y), entry * (1 + x)
        hit_s, hit_t = l <= s, h >= t
        i_s = hit_s.argmax() if hit_s.any() else len(l)
        i_t = hit_t.argmax() if hit_t.any() else len(l)
        if i_s < len(l) and i_s <= i_t:
            r, how = min(s, o[i_s]) / entry - 1, "stop"
        elif i_t < len(l):
            r, how = x, "target"
        else:
            r, how = last / entry - 1, "close"
        res[(x, y)] = (r, how)
    out[(sym, day)] = res
print(f"{len(out):,} stock-days simulated")

close = pd.Series({k: v["close"] for k, v in out.items()}).unstack(0).sort_index()
vol = close.pct_change(fill_method=None).rolling(20, min_periods=20).std().shift(1)  # prior days only
picks = {d: list(vol.loc[d].dropna().nlargest(10).index) for d in vol.index if vol.loc[d].notna().sum() >= 10}


def account(key):
    """Daily account return: 10 slots x 10%, after costs; also the trades."""
    days, trades = {}, []
    for d, syms in picks.items():
        rs = []
        for s in syms:
            v = out.get((s, d))
            if v is None:
                continue
            r, how = (v["hold"], "close") if key == "hold" else v[key]
            rs.append(r - 2 * COST)
            trades.append((d, r - 2 * COST, how))
        days[d] = sum(rs) / 10
    return pd.Series(days), pd.DataFrame(trades, columns=["day", "ret", "how"])


def summary(daily):
    e = (1 + daily).cumprod()
    yrs = len(daily) / 252
    t = daily.mean() / daily.std() * np.sqrt(len(daily))
    return daily.mean(), t, e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min()


TRAIN = lambda s: s[s.index < "2023-01-01"]
TEST = lambda s: s[s.index >= "2023-01-01"]
rows = []
for c in COMBOS:
    daily, _ = account(c)
    rows.append((c, summary(TRAIN(daily)), summary(TEST(daily))))
print("\n take  stop | 2020-22 avg day, per yr | 2023-26 avg day, per yr")
for (x, y), tr, te in rows:
    print(f" +{x:.0%}  -{y:.0%} | {tr[0]:+.3%} {tr[2]:+7.1%} | {te[0]:+.3%} {te[2]:+7.1%}")

best = max(rows, key=lambda r: r[1][0])
(x, y) = best[0]
daily, trades = account((x, y))
hold_daily, _ = account("hold")
m, t, cagr, dd = summary(TEST(daily))
hm, ht, hcagr, hdd = summary(TEST(hold_daily))
tt = trades[trades.day >= "2023-01-01"]
rank = pd.Series({r[0]: r[2][0] for r in rows}).rank(ascending=False)[(x, y)]
agree = pd.Series([r[1][0] for r in rows]).corr(pd.Series([r[2][0] for r in rows]), method="spearman")
print(f"\nChosen on 2020-2022: take profit +{x:.0%}, stop -{y:.0%}")
print(f"Unseen 2023-2026: avg day {m:+.3%} (t={t:+.1f}), {cagr:+.1%}/yr, worst drop {dd:.0%}; ranks {int(rank)} of 16")
print(f"  plain hold 09:35->close, same stocks: avg day {hm:+.3%} (t={ht:+.1f}), {hcagr:+.1%}/yr, worst drop {hdd:.0%}")
print(f"  per trade: avg {tt.ret.mean():+.3%}, winners {np.mean(tt.ret > 0):.0%}, exits "
      + ", ".join(f"{k} {v:.0%}" for k, v in tt.how.value_counts(normalize=True).items()))
print(f"  rank agreement of the 16 settings between halves: {agree:+.2f}")
ok = m > 0 and t >= 2 and m > hm and cagr > 0.05
print("PASS" if ok else "no edge (fails the pre-registered bar)")

# ---- secondary: only on days the live breadth gate is on ----
src = pathlib.Path(__file__).with_name("sp500_backtest.py").read_text(encoding="utf-8")
exec(src.split("def simulate")[0])  # defines breadth + signal()
gate = signal(0.55, 0.50).shift(1, fill_value=False)  # yesterday's close decides today
gate.index = gate.index.strftime("%Y-%m-%d")
on = TEST(daily)[gate.reindex(TEST(daily).index).fillna(False).astype(bool)]
gm, gt, _, _ = summary(on)
print(f"\nSecondary, gate-on days only in 2023-2026: {len(on)} days, avg day {gm:+.3%} (t={gt:+.1f})")
