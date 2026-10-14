"""Fast drop trigger -> high-volatility long bets (VOL-DROP-PLAN.md). Daily OHLC, next-open entry,
TP/SL on the day's high/low (stop first if both), time cap H days, 5 bps/side, 10% slots."""
import itertools
import pickle
import numpy as np
import pandas as pd
import yfinance as yf

FUNDS = ["SQQQ", "SPXU", "SOXS", "UVXY"]
COST = 0.0005
TRAIN = ("2011-11-01", "2019-01-01")
TEST = ("2019-01-01", "2026-09-01")

raw = yf.download(["SPY"] + FUNDS, start="2011-01-01", auto_adjust=True, progress=False)
raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
O, H, L, C = raw["Open"], raw["High"], raw["Low"], raw["Close"]
days = C.index

# point-in-time S&P 500 breadth (same construction as sp500_backtest.py)
closes = pickle.load(open("C:/OpenMind/cerebral/data/research_sp500_close.pkl", "rb"))
px = pd.DataFrame(closes).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()].reindex(days)
hist = pd.read_csv("C:/OpenMind/cerebral/data/research_sp500_hist.csv", parse_dates=["date"]).sort_values("date")
rows = {d: set(t.split(",")) for d, t in zip(hist["date"], hist["tickers"])}
cds = sorted(rows)
members = pd.DataFrame(False, index=days, columns=px.columns)
for i, d in enumerate(cds):
    end = cds[i + 1] if i + 1 < len(cds) else days[-1] + pd.Timedelta(days=1)
    members.loc[(days >= d) & (days < end), [c for c in rows[d] if c in members.columns]] = True
ma50 = px.rolling(50, min_periods=50).mean()
valid = members & ma50.notna() & px.notna()
breadth = ((px > ma50) & valid).sum(axis=1) / valid.sum(axis=1)

TRIGGERS = {
    "A: SPY -2% day": C["SPY"].pct_change() <= -0.02,
    "B: breadth -15pts/3d": (breadth - breadth.shift(3)) <= -0.15,
}


def bets(trig, fund, tp, sl, h, start, end):
    """[(exit_day, net_return)] for one setting; one open bet per fund at a time."""
    out, busy_until, n = [], -1, len(days)
    o, hi, lo, c = O[fund].values, H[fund].values, L[fund].values, C[fund].values
    for t in np.flatnonzero(trig.values):
        d = days[t]
        if d < pd.Timestamp(start) or d >= pd.Timestamp(end):
            continue
        i = t + 1
        if i >= n or i <= busy_until or np.isnan(o[i]):
            continue
        entry, take, stop = o[i], o[i] * (1 + tp), o[i] * (1 - sl)
        last = min(i + h - 1, n - 1)
        ret, j_exit = None, last
        for j in range(i, last + 1):
            if j > i and o[j] <= stop:      # gapped through the stop
                ret, j_exit = o[j] / entry - 1, j; break
            if j > i and o[j] >= take:      # gapped through the target
                ret, j_exit = o[j] / entry - 1, j; break
            if lo[j] <= stop:               # stop first if both touch (conservative)
                ret, j_exit = -sl, j; break
            if hi[j] >= take:
                ret, j_exit = tp, j; break
        if ret is None:
            ret = c[last] / entry - 1
        out.append((days[j_exit], ret - 2 * COST))
        busy_until = j_exit
    return out


def account_cagr(bs, start, end):
    eq = 1.0
    for _, r in sorted(bs):
        eq *= 1 + 0.10 * r  # 10% slot
    yrs = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    return eq ** (1 / yrs) - 1


rows_out = []
for (tname, trig), fund, tp, sl, h in itertools.product(TRIGGERS.items(), FUNDS, (0.05, 0.10, 0.20), (0.05, 0.10), (3, 10)):
    tr, te = bets(trig, fund, tp, sl, h, *TRAIN), bets(trig, fund, tp, sl, h, *TEST)
    rows_out.append({
        "trigger": tname, "fund": fund, "tp": tp, "sl": sl, "h": h,
        "n_tr": len(tr), "avg_tr": np.mean([r for _, r in tr]) if tr else np.nan,
        "n_te": len(te), "avg_te": np.mean([r for _, r in te]) if te else np.nan,
        "win_te": np.mean([r > 0 for _, r in te]) if te else np.nan,
        "cagr_te": account_cagr(te, *TEST),
    })
df = pd.DataFrame(rows_out)
pd.set_option("display.width", 200)

for name, (s, e) in (("2011-18", TRAIN), ("2019-26", TEST)):
    spy = C["SPY"][(days >= s) & (days < e)]
    yrs = (spy.index[-1] - spy.index[0]).days / 365.25
    print(f"SPY buy & hold {name}: {(spy.iloc[-1] / spy.iloc[0]) ** (1 / yrs) - 1:+.1%}/yr")
for tname, trig in TRIGGERS.items():
    print(f"{tname}: {int(trig[(days >= TRAIN[0]) & (days < TEST[1])].sum())} trigger days 2011-26")

print("\nAll 96 settings: average bet")
print(f"  2011-18 positive: {(df.avg_tr > 0).sum()}/96   2019-26 positive: {(df.avg_te > 0).sum()}/96")
rho = df["avg_tr"].corr(df["avg_te"], method="spearman")
print(f"  rank agreement between halves (Spearman): {rho:+.2f}")

eligible = df[df.n_tr >= 20].sort_values("avg_tr", ascending=False)
print("\nTop 8 on 2011-18 (>=20 bets), and how they did on 2019-26:")
print(eligible.head(8).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))

best = eligible.iloc[0]
print(f"\nCHOSEN on 2011-18: {best.trigger} | {best.fund} | TP {best.tp:.0%} SL {best.sl:.0%} H {best.h}d")
print(f"  2011-18: {best.n_tr} bets, avg {best.avg_tr:+.2%}")
print(f"  2019-26: {best.n_te} bets, avg {best.avg_te:+.2%}, winners {best.win_te:.0%}, account {best.cagr_te:+.2%}/yr")
p1 = best.avg_te > 0 and best.n_te >= 20
p2 = best.cagr_te > 0
p3 = rho >= 0.3
print(f"\nPASS BAR: avg bet>0 (>=20 bets) {p1}; account grows {p2}; rank agreement>=0.3 {p3}"
      f"  -> {'PASS' if p1 and p2 and p3 else 'FAIL'}")
