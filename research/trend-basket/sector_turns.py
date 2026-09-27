"""Does sector news shift BEFORE sector bull/bear turns? Exactly as pre-registered in
SECTOR-TURNS-PLAN.md (2026-09-27). Loading mirrors news_analysis.py."""
import json
import pickle
import sqlite3

import numpy as np
import pandas as pd
import yfinance as yf

DATA = "C:/OpenMind/cerebral/data/"
COST = 0.0002
ETF = {"Technology": "XLK", "Financial Services": "XLF", "Energy": "XLE", "Healthcare": "XLV",
       "Industrials": "XLI", "Basic Materials": "XLB", "Consumer Defensive": "XLP",
       "Utilities": "XLU", "Consumer Cyclical": "XLY"}

# ---- point-in-time membership on trading days ----
px = pd.DataFrame(pickle.load(open(DATA + "research_sp500_close.pkl", "rb"))).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()]
days = px.index[px.index >= "2015-10-01"]
hist = pd.read_csv(DATA + "research_sp500_hist.csv", parse_dates=["date"]).sort_values("date")
rows = {d: set(t.split(",")) for d, t in zip(hist["date"], hist["tickers"])}
chg = sorted(rows)
members = pd.DataFrame(False, index=days, columns=px.columns)
for i, d in enumerate(chg):
    end = chg[i + 1] if i + 1 < len(chg) else days[-1] + pd.Timedelta(days=1)
    members.loc[(days >= d) & (days < end), [c for c in rows[d] if c in members.columns]] = True

# ---- (article, member) pairs by signal day ----
con = sqlite3.connect(DATA + "news_scores.db")
con.execute(f"ATTACH '{DATA}news_history.db' AS h")
art = pd.read_sql("SELECT n.created_at, n.symbols, s.score FROM h.news n JOIN scores s USING(id)", con)
t = pd.to_datetime(art["created_at"], utc=True).dt.tz_convert("America/New_York")
cal = t.dt.tz_localize(None).dt.normalize() + pd.to_timedelta((t.dt.hour >= 16).astype(int), unit="D")
pos = np.searchsorted(days.values, cal.values.astype("datetime64[ns]"))
art = art[pos < len(days)].copy()
art["day"] = days[pos[pos < len(days)]]
art["sym"] = art["symbols"].map(json.loads)
long = art[["day", "score", "sym"]].explode("sym")
long = long[long["sym"].isin(members.columns)]
long = long[members.to_numpy()[days.get_indexer(long["day"]), members.columns.get_indexer(long["sym"])]]
smap = pickle.load(open(DATA + "research_sector_map.pkl", "rb"))
long["month"] = long["day"].dt.to_period("M")
mkt_tone = long.groupby("month")["score"].mean()
long["sector"] = long["sym"].map(smap)
long = long[long["sector"].isin(ETF)]
g = long.groupby(["month", "sector"])["score"]
ssum, cnt = g.sum().unstack(), g.size().unstack()
ssum = ssum[ssum.index >= pd.Period("2016-01", "M")]
cnt = cnt.reindex(ssum.index)
print(f"(article, member) pairs in the 9 sectors: {len(long):,}; months {len(ssum)}; "
      f"min monthly pairs per sector {int(cnt.min().min())}")

tone = ssum / cnt
raw = {"tone": tone, "volume": np.log(cnt), "rel tone": tone.sub(mkt_tone.reindex(tone.index), axis=0)}
abn = {k: v - v.shift(1).rolling(12, min_periods=6).mean() for k, v in raw.items()}

# ---- turning points on the sector funds ----
fund = yf.download(list(ETF.values()), start="1999-01-01", auto_adjust=True, progress=False)["Close"].dropna()
fund.index = pd.to_datetime(fund.index).tz_localize(None)


def turns_20(s):
    out, state, ext, ext_d = [], "bull", s.iloc[0], s.index[0]
    for d, v in s.items():
        if state == "bull":
            if v > ext:
                ext, ext_d = v, d
            if v <= ext * 0.8:
                out.append(("bull->bear", ext_d)); state, ext, ext_d = "bear", v, d
        else:
            if v < ext:
                ext, ext_d = v, d
            if v >= ext * 1.2:
                out.append(("bear->bull", ext_d)); state, ext, ext_d = "bull", v, d
    return out


def turns_200(s):
    above = (s > s.rolling(200).mean()).iloc[200:]
    a = above.to_numpy()
    out = []
    for i in range(1, len(a) - 19):
        if a[i] != a[i - 1] and (a[i:i + 20] == a[i]).all():
            out.append(("bear->bull" if a[i] else "bull->bear", above.index[i]))
    return out


events = []
for sec, tk in ETF.items():
    for rule, fn in (("20% rule", turns_20), ("200-day", turns_200)):
        events += [(rule, kind, sec, d) for kind, d in fn(fund[tk])]
ev = pd.DataFrame(events, columns=["rule", "kind", "sector", "date"])
ev["month"] = ev["date"].dt.to_period("M")


def window(a, sec, m, lo, hi):
    """Mean of abnormal values in months m-hi .. m-lo; NaN unless all present."""
    v = a[sec].reindex([m - k for k in range(lo, hi + 1)])
    return v.mean() if v.notna().all() else np.nan


# ---- test b: event study ----
print("\n=== TEST b: abnormal news measure in the months before a sector turn, vs normal times ===")
print("(expected: tone/rel tone FALL before bull->bear, RISE before bear->bull; volume two-sided)")
print(f"{'rule':9s} {'turn':10s} {'measure':9s} {'window':7s} {'n':>3s} {'months':>6s} {'diff':>8s} {'t':>6s} "
      f"{'16-20':>8s} {'21-26':>8s}  verdict")
hits = 0
for rule in ("20% rule", "200-day"):
    for kind in ("bull->bear", "bear->bull"):
        e = ev[(ev.rule == rule) & (ev.kind == kind)]
        for meas, a in abn.items():
            base = a.stack().mean()
            for lo, hi, lab in ((1, 3, "-3..-1"), (4, 6, "-6..-4")):
                x = pd.Series([window(a, s, m, lo, hi) for s, m in zip(e.sector, e.month)], index=e.index) - base
                ok = x.notna()
                x, dates = x[ok], e.date[ok]
                n = len(x)
                if n < 3:
                    print(f"{rule:9s} {kind:10s} {meas:9s} {lab:7s} {n:3d}  too few events")
                    continue
                tt = x.mean() / (x.std() / np.sqrt(n))
                h1, h2 = x[dates < "2021-01-01"].mean(), x[dates >= "2021-01-01"].mean()
                want = 0 if meas == "volume" else (-1 if kind == "bull->bear" else 1)
                right_dir = tt * want > 0 if want else True
                same = np.sign(h1) == np.sign(h2) == np.sign(tt)
                verdict = "SHIFT" if abs(tt) >= 2 and right_dir and same else "-"
                hits += verdict == "SHIFT"
                scale = 1 if meas == "volume" else 100  # tone in hundredths of a score point
                print(f"{rule:9s} {kind:10s} {meas:9s} {lab:7s} {n:3d} {e.month[ok].nunique():6d} "
                      f"{x.mean() * scale:+8.2f} {tt:+6.1f} {h1 * scale:+8.2f} {h2 * scale:+8.2f}  {verdict}")
print(f"tests passing: {hits} of 24 (about 1 expected by chance). tone diffs x100; volume in log points.")

# ---- test c: tradeable rule ----
print("\n=== TEST c: hold a sector fund only while its 3-month tone >= its 12-month tone ===")
fm = fund.resample("ME").last()
fm.index = fm.index.to_period("M")
r = fm.pct_change().shift(-1)  # return of the month after the signal month
spy = yf.download("SPY", start="2015-12-01", auto_adjust=True, progress=False)["Close"].squeeze().resample("ME").last()
spy.index = spy.index.to_period("M")
spy_r = spy.pct_change().shift(-1)
t3 = ssum.rolling(3).sum() / cnt.rolling(3).sum()
t12 = ssum.rolling(12).sum() / cnt.rolling(12).sum()
hold = (t3 >= t12).where(t12.notna()).rename(columns=ETF)
sig = hold.index[(hold.index >= pd.Period("2016-12", "M")) & (hold.index <= pd.Period("2026-07", "M"))]
w = hold.loc[sig].astype(float) / len(ETF)
rr = r.loc[sig, list(ETF.values())]
rule_r = (w * rr).sum(axis=1) - w.diff().abs().sum(axis=1).fillna(w.iloc[0].sum()) * COST
ew_r = rr.mean(axis=1)
bench = pd.DataFrame({"news rule": rule_r, "EW hold": ew_r, "SPY": spy_r.reindex(sig)})
bench.index = sig + 1  # label by the month the return is earned


def st(x):
    e = (1 + x).cumprod()
    return e.iloc[-1] ** (12 / len(x)) - 1, (e / e.cummax() - 1).min(), x.mean() / x.std() * np.sqrt(12)


print(f"time invested: {w.sum(axis=1).mean():.0%} on average")
for lab, a, b in (("2017-2020", "2017-01", "2020-12"), ("2021-2026", "2021-01", "2026-08"), ("all", "2017-01", "2026-08")):
    part = bench.loc[pd.Period(a, "M"):pd.Period(b, "M")]
    print(f"{lab}: " + " | ".join(f"{k} {c:+.1%}/yr maxDD {dd:.0%} Sharpe {sh:.2f}"
                                   for k, (c, dd, sh) in ((k, st(part[k])) for k in part)))
wins = all(st(bench.loc[pd.Period(a, "M"):pd.Period(b, "M")]["news rule"])[0] >
           max(st(bench.loc[pd.Period(a, "M"):pd.Period(b, "M")][k])[0] for k in ("EW hold", "SPY"))
           for a, b in (("2017-01", "2020-12"), ("2021-01", "2026-08")))
print("PASS" if wins else "no edge (fails the pre-registered bar)")

# ---- POST-HOC (added after seeing results, not pre-registered) ----
# Test b's two passes were abnormal VOLUME before 20%-rule troughs. Is volume simply high all
# through a bear phase (a crash makes news), rather than specifically ahead of the bottom?
print("\n=== POST-HOC: abnormal volume by 20%-rule phase (not pre-registered) ===")
av = abn["volume"]
for sec, tk in ETF.items():
    tt = sorted((d, k) for k, d in turns_20(fund[tk]))
    st_m = pd.Series(index=av.index, dtype=object)
    for m in av.index:
        prior = [k for d, k in tt if d.to_period("M") < m]
        st_m[m] = "bear" if prior and prior[-1] == "bull->bear" else "bull"
    av.loc[:, sec + "_state"] = st_m
states = pd.concat([pd.DataFrame({"v": av[s], "state": av[s + "_state"]}) for s in ETF]).dropna()
for s, grp in states.groupby("state"):
    print(f"{s}: {len(grp)} sector-months, mean abnormal volume {grp.v.mean():+.3f} log points")
