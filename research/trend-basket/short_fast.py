"""Fast drop trigger -> short the most volatile, weakest S&P 500 members (SHORT-FAST-PLAN.md).
Closes only: short at t+1 close, TP/SL/time exits checked at closes, 10% slots, 5 bps/side."""
import itertools
import pickle
import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.0005
TRAIN = ("2005-01-01", "2016-01-01")
TEST = ("2016-01-01", "2026-09-01")

spy = yf.download("SPY", start="2004-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
days = spy.index
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
ret = px.pct_change(fill_method=None)
ma50 = px.rolling(50, min_periods=50).mean()
valid = members & ma50.notna() & px.notna()
breadth = ((px > ma50) & valid).sum(axis=1) / valid.sum(axis=1)
weak = (px.pct_change(20, fill_method=None) * ret.rolling(20).std()).where(members)  # most negative = pick

TRIGGERS = {
    "A: SPY -2% day": spy.pct_change() <= -0.02,
    "B: breadth -15pts/3d": (breadth - breadth.shift(3)) <= -0.15,
}
P = px.values
COLS = list(px.columns)


def run(trig, tp, sl, h, start, end):
    """Returns (list of net short returns by exit, account CAGR)."""
    sig = trig.values
    cash, pos, bets, eq = 1.0, {}, [], []
    s_i, e_i = days.searchsorted(pd.Timestamp(start)), days.searchsorted(pd.Timestamp(end))
    for i in range(s_i, e_i):
        row = P[i]
        for j in list(pos):
            p = pos[j]
            c = row[j]
            if np.isnan(c):
                p["gap"] += 1
                if p["gap"] > 5:
                    r = 1 - p["last"] / p["entry"] - 2 * COST
                    cash += p["alloc"] * (1 + r); bets.append(r); pos.pop(j)
                continue
            p["gap"], p["last"] = 0, c
            move = 1 - c / p["entry"]  # short profit so far
            if move >= tp or move <= -sl or i - p["i"] >= h:
                r = move - 2 * COST
                cash += p["alloc"] * (1 + r); bets.append(r); pos.pop(j)
        equity = cash + sum(p["alloc"] * (1 + 1 - p["last"] / p["entry"]) for p in pos.values())
        # enter today at the close if YESTERDAY was a trigger day (signal at t, short at t+1 close)
        if i > 0 and sig[i - 1] and len(pos) < 10:
            sc = weak.iloc[i - 1].dropna().sort_values()  # as of the trigger day's close
            for sym in sc.index:
                if len(pos) >= 10:
                    break
                j = COLS.index(sym)
                if j in pos or np.isnan(row[j]):
                    continue
                alloc = min(equity * 0.10, cash)
                if alloc <= 1e-9:
                    break
                cash -= alloc
                pos[j] = {"i": i, "entry": row[j], "last": row[j], "gap": 0, "alloc": alloc}
        equity = cash + sum(p["alloc"] * (1 + 1 - p["last"] / p["entry"]) for p in pos.values())
        eq.append(equity)
    yrs = (days[e_i - 1] - days[s_i]).days / 365.25
    return bets, eq[-1] ** (1 / yrs) - 1, min(np.array(eq) / np.maximum.accumulate(eq) - 1)


out = []
for (tname, trig), tp, sl, h in itertools.product(TRIGGERS.items(), (0.05, 0.10, 0.20), (0.05, 0.10), (3, 10)):
    tr, c_tr, _ = run(trig, tp, sl, h, *TRAIN)
    te, c_te, dd_te = run(trig, tp, sl, h, *TEST)
    out.append({"trigger": tname, "tp": tp, "sl": sl, "h": h,
                "n_tr": len(tr), "avg_tr": np.mean(tr) if tr else np.nan, "cagr_tr": c_tr,
                "n_te": len(te), "avg_te": np.mean(te) if te else np.nan,
                "win_te": np.mean(np.array(te) > 0) if te else np.nan, "cagr_te": c_te, "dd_te": dd_te})
df = pd.DataFrame(out)
pd.set_option("display.width", 220)

for name, (s, e) in (("2005-15", TRAIN), ("2016-26", TEST)):
    x = spy[(days >= s) & (days < e)]
    yrs = (x.index[-1] - x.index[0]).days / 365.25
    print(f"SPY buy & hold {name}: {(x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1:+.1%}/yr")
for tname, trig in TRIGGERS.items():
    print(f"{tname}: {int(trig[(days >= TRAIN[0]) & (days < TEST[1])].sum())} trigger days 2005-26")

print(f"\nAll 24 settings, average bet positive: 2005-15 {(df.avg_tr > 0).sum()}/24, 2016-26 {(df.avg_te > 0).sum()}/24")
rho = df["avg_tr"].corr(df["avg_te"], method="spearman")
print(f"rank agreement between halves (Spearman): {rho:+.2f}")
print("\nAll settings, sorted by 2005-15 average bet:")
print(df.sort_values("avg_tr", ascending=False).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))

best = df[df.n_tr >= 20].sort_values("avg_tr", ascending=False).iloc[0]
print(f"\nCHOSEN on 2005-15: {best.trigger} | TP {best.tp:.0%} SL {best.sl:.0%} H {best.h}d")
print(f"  2005-15: {best.n_tr} bets, avg {best.avg_tr:+.2%}, account {best.cagr_tr:+.1%}/yr")
print(f"  2016-26: {best.n_te} bets, avg {best.avg_te:+.2%}, winners {best.win_te:.0%}, "
      f"account {best.cagr_te:+.1%}/yr, worst drop {best.dd_te:.0%}")
p1, p2, p3 = best.avg_te > 0 and best.n_te >= 20, best.cagr_te > 0, rho >= 0.3
print(f"\nPASS BAR: avg bet>0 {p1}; account grows {p2}; rank agreement>=0.3 {p3} -> {'PASS' if p1 and p2 and p3 else 'FAIL'}")
