"""Bear-regime holding test (BEAR-HOLD-PLAN.md): the live trend basket on the point-in-time S&P 500,
with idle cash held in SH / TLT / GLD while breadth is weak (cross below 45%, ON while < 50%)."""
import pickle
import numpy as np
import pandas as pd
import yfinance as yf

COST, TRAIL, MAX_HOLD, N_SLOTS = 0.0002, 0.12, 20, 10
ASSETS = ["SH", "TLT", "GLD"]

closes = pickle.load(open("C:/OpenMind/cerebral/data/research_sp500_close.pkl", "rb"))
px = pd.DataFrame(closes).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()]
etf = yf.download(["SPY"] + ASSETS, start="2004-01-01", auto_adjust=True, progress=False)["Close"]
etf.index = pd.to_datetime(etf.index).tz_localize(None).normalize()
px = px.reindex(etf.index)

hist = pd.read_csv("C:/OpenMind/cerebral/data/research_sp500_hist.csv", parse_dates=["date"]).sort_values("date")
rows = {d: set(t.split(",")) for d, t in zip(hist["date"], hist["tickers"])}
change_days = sorted(rows)
members = pd.DataFrame(False, index=px.index, columns=px.columns)
for i, d in enumerate(change_days):
    end = change_days[i + 1] if i + 1 < len(change_days) else px.index[-1] + pd.Timedelta(days=1)
    cols = [c for c in rows[d] if c in members.columns]
    members.loc[(members.index >= d) & (members.index < end), cols] = True

ret = px.pct_change(fill_method=None)
ma50 = px.rolling(50, min_periods=50).mean()
valid = members & ma50.notna() & px.notna()
breadth = ((px > ma50) & valid).sum(axis=1) / valid.sum(axis=1)
score = (px.pct_change(20, fill_method=None) * ret.rolling(20).std()).where(members)


def regime(on_below, stay_below=None, cross_above=None, stay_above=None):
    out, on, prev = [], False, np.nan
    for v in breadth.fillna(0.5):
        if cross_above is not None:  # bull: cross above, stay while above
            crossed = v > cross_above and not (prev > cross_above)
            on = crossed or (on and v > stay_above)
        else:  # bear: cross below, stay while below
            crossed = v < on_below and not (prev < on_below)
            on = crossed or (on and v < stay_below)
        out.append(on)
        prev = v
    return pd.Series(out, index=breadth.index)


BULL = regime(None, cross_above=0.55, stay_above=0.50)
BEAR = regime(0.45, stay_below=0.50)
STINTS = []  # asset return per bear stint, from the last simulate() call


def simulate(asset, start, end, sleeve=None):
    STINTS.clear()
    buy = BULL.shift(1, fill_value=False)
    bear = (BEAR if sleeve is None else sleeve).shift(1, fill_value=False)
    sc = score.shift(1)
    a_px = etf[asset] if asset else None
    cash, units_a, pos, eq, stint_start = 1.0, 0.0, {}, [], None
    for i, d in enumerate(px.index):
        if d < pd.Timestamp(start) or d >= pd.Timestamp(end):
            continue
        row = px.iloc[i]
        for sym in list(pos):
            p, c = pos[sym], row[sym]
            if pd.isna(c):
                p["gap"] += 1
                if p["gap"] > 5:
                    cash += p["units"] * p["last"] * (1 - COST)
                    pos.pop(sym)
                continue
            p["gap"], p["last"] = 0, c
            if c <= p["peak"] * (1 - TRAIL) or i - p["i"] >= MAX_HOLD:
                cash += p["units"] * c * (1 - COST)
                pos.pop(sym)
            else:
                p["peak"] = max(p["peak"], c)
        a = a_px.iloc[i] if asset is not None and pd.notna(a_px.iloc[i]) else None
        # bear sleeve: hold all idle cash in the asset while the regime is on
        if a is not None:
            if bear.iloc[i]:
                if stint_start is None:
                    stint_start = a
                if cash > 1e-9:
                    units_a += cash * (1 - COST) / a
                    cash = 0.0
            elif units_a > 0:
                cash += units_a * a * (1 - COST)
                units_a = 0.0
                STINTS.append(a / stint_start - 1)
                stint_start = None
        equity = cash + units_a * (a or 0) + sum(p["units"] * p["last"] for p in pos.values())
        if buy.iloc[i] and len(pos) < N_SLOTS:
            if units_a > 0 and a is not None:  # bull and bear never overlap, but be safe
                cash += units_a * a * (1 - COST)
                units_a = 0.0
            for sym in sc.iloc[i].dropna().sort_values(ascending=False).index:
                if len(pos) >= N_SLOTS:
                    break
                if sym in pos or pd.isna(row[sym]):
                    continue
                alloc = min(equity * 0.10, cash)
                if alloc <= 1e-9:
                    break
                cash -= alloc
                pos[sym] = {"i": i, "peak": row[sym], "last": row[sym], "gap": 0, "units": alloc * (1 - COST) / row[sym]}
        equity = cash + units_a * (a or 0) + sum(p["units"] * p["last"] for p in pos.values())
        eq.append((d, equity))
    e = pd.Series(dict(eq))
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252)


print(f"bear regime ON {BEAR['2006-07':].mean():.0%} of days, bull/basket regime ON {BULL['2006-07':].mean():.0%}")
PERIODS = [("2006-07-01", "2016-01-01"), ("2016-01-01", "2026-09-01"), ("2006-07-01", "2026-09-01")]
res = {}
for start, end in PERIODS:
    s = etf["SPY"][(etf.index >= start) & (etf.index < end)]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    print(f"\n=== {start[:4]}..{end[:4]} ===  SPY buy&hold CAGR {(s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1:+.1%}  maxDD {(s / s.cummax() - 1).min():.0%}")
    for asset in [None] + ASSETS:
        c, d, sh = simulate(asset, start, end)
        res[(asset, start)] = (c, d)
        extra = ""
        if asset:
            st = np.array(STINTS)
            extra = f"  bear stints {len(st)}: avg {st.mean():+.2%}, winners {np.mean(st > 0):.0%}" if len(st) else ""
        print(f"basket + {asset or 'cash':4s}  CAGR {c:+6.1%}  maxDD {d:5.0%}  Sharpe {sh:4.2f}{extra}")

print("\n=== pass bar (BEAR-HOLD-PLAN.md) ===")
h1, h2, full = (p[0] for p in PERIODS)
for asset in ASSETS:
    simulate(asset, *PERIODS[2])
    st = np.array(STINTS)
    c1 = res[(asset, h1)][0] > res[(None, h1)][0]
    c2 = res[(asset, h2)][0] > res[(None, h2)][0]
    dd = res[(asset, full)][1] >= res[(None, full)][1] - 0.01
    bets = len(st) > 0 and st.mean() > 0
    print(f"{asset}: beats cash 2006-15 {c1}, 2016-26 {c2}; drawdown ok {dd}; stints avg>0 {bets}  -> {'PASS' if c1 and c2 and dd and bets else 'FAIL'}")


# ---- POST-HOC (not in BEAR-HOLD-PLAN.md, added 2026-10-09 after the GLD pass) ----
# Is it the breadth TIMING or just gold going up? Two controls on the full period:
# (a) gold in idle cash whenever the basket isn't buying; (b) the same bear-regime days shifted
# to random times (circular shift, same number and length of stints), 100 draws.
if __name__ == "__main__":
    full = PERIODS[2]
    g = etf["GLD"][(etf.index >= full[0]) & (etf.index < full[1])]
    yrs = (g.index[-1] - g.index[0]).days / 365.25
    print(f"\nPOST-HOC: GLD buy&hold {full[0][:4]}..{full[1][:4]} CAGR {(g.iloc[-1] / g.iloc[0]) ** (1 / yrs) - 1:+.1%}")
    c_real = simulate("GLD", *full)[0]
    c_idle = simulate("GLD", *full, sleeve=~BULL)[0]
    print(f"basket + GLD only in weak breadth (tested rule): CAGR {c_real:+.1%}")
    print(f"basket + GLD whenever basket not buying:         CAGR {c_idle:+.1%}")
    rng = np.random.default_rng(0)
    n = len(BEAR); draws = []
    for _ in range(100):
        k = int(rng.integers(250, n - 250))
        draws.append(simulate("GLD", *full, sleeve=pd.Series(np.roll(BEAR.values, k), index=BEAR.index))[0])
    draws = np.array(draws)
    print(f"same gold days at random times (100 draws): median {np.median(draws):+.1%}, "
          f"10-90% {np.quantile(draws,.1):+.1%}..{np.quantile(draws,.9):+.1%}; "
          f"share of draws >= tested rule: {np.mean(draws >= c_real):.0%}")
