"""News -> where-to-be test, exactly as pre-registered in NEWS-TEST-PLAN.md (2026-09-25).
Signal day: an article counts toward trading day D if published before 16:00 ET on D (after the
previous close); it trades at D's close and earns D+1's return. 2 bps/side on category turnover."""
import json
import pickle
import re
import sqlite3

import numpy as np
import pandas as pd

DATA = "C:/OpenMind/cerebral/data/"
COST = 0.0002
THEMES = {
    "AI": r"\b(AI|artificial intelligence)\b", "crypto": r"\b(crypto\w*|bitcoin)\b",
    "EV": r"\b(EVs?|electric vehicles?)\b", "oil": r"\b(oil|crude|OPEC)\b",
    "rates/Fed": r"\b(Fed|interest rates?|rate hike|rate cut)\b", "tariffs": r"\b(tariffs?|trade war)\b",
    "earnings beat": r"\bbeats?\b", "earnings miss": r"\bmiss(es)?\b", "FDA": r"\b(FDA|approval)\b",
    "M&A": r"\b(merger|acquisition|acquires?|buyout)\b",
}

# ---- prices + point-in-time membership ----
closes = pickle.load(open(DATA + "research_sp500_close.pkl", "rb"))
px = pd.DataFrame(closes).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()]
px = px[px.index >= "2015-10-01"]
days = px.index
ret_next = px.pct_change(fill_method=None).shift(-1)  # return earned by holding from D's close to D+1's
hist = pd.read_csv(DATA + "research_sp500_hist.csv", parse_dates=["date"]).sort_values("date")
rows = {d: set(t.split(",")) for d, t in zip(hist["date"], hist["tickers"])}
chg = sorted(rows)
members = pd.DataFrame(False, index=days, columns=px.columns)
for i, d in enumerate(chg):
    end = chg[i + 1] if i + 1 < len(chg) else days[-1] + pd.Timedelta(days=1)
    members.loc[(days >= d) & (days < end), [c for c in rows[d] if c in members.columns]] = True
ret_next = ret_next.where(members)
spy = __import__("yfinance").download("SPY", start="2015-10-01", auto_adjust=True, progress=False)["Close"].squeeze()
spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
spy_next = spy.pct_change().shift(-1).reindex(days)

# ---- articles -> (signal day, symbol, score) ----
con = sqlite3.connect(DATA + "news_scores.db")
con.execute(f"ATTACH '{DATA}news_history.db' AS h")
art = pd.read_sql("SELECT n.id, n.created_at, n.headline, n.symbols, s.score FROM h.news n JOIN scores s USING(id)", con)
t = pd.to_datetime(art["created_at"], utc=True).dt.tz_convert("America/New_York")
cal = t.dt.tz_localize(None).dt.normalize() + pd.to_timedelta((t.dt.hour >= 16).astype(int), unit="D")
pos = np.searchsorted(days.values, cal.values.astype("datetime64[ns]"))
art = art[pos < len(days)].copy()
art["day"] = days[pos[pos < len(days)]]
art["syms"] = art["symbols"].map(json.loads)
long = art[["id", "day", "score", "syms"]].explode("syms").rename(columns={"syms": "sym"})
long = long[long["sym"].isin(px.columns)]
long = long[[members.at[d, s] for d, s in zip(long["day"], long["sym"])]]
print(f"articles scored {len(art):,}; (article, S&P member) pairs {len(long):,}; days {long['day'].nunique()}")


def stats(r):
    r = r.dropna()
    e = (1 + r).cumprod()
    yrs = len(r) / 252
    return e.iloc[-1] ** (1 / yrs) - 1, (e / e.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252)


HALVES = [("2016-01-01", "2021-01-01"), ("2021-01-01", "2026-09-01")]

# ---- Test 1: stock level ----
print("\n=== TEST 1: stock level -- top minus bottom quintile of each day's news score ===")
fwd = {h: (px.shift(-h) / px - 1).where(members) for h in (1, 5, 20)}
daily = long.groupby(["day", "sym"])["score"].mean().reset_index()
res = {h: [] for h in fwd}
for d, g in daily.groupby("day"):
    if len(g) < 25:
        continue
    q = pd.qcut(g["score"].rank(method="first"), 5, labels=False)
    for h, f in fwd.items():
        if d not in f.index:
            continue
        r = f.loc[d, g["sym"]].to_numpy()
        top, bot = np.nanmean(r[q.to_numpy() == 4]), np.nanmean(r[q.to_numpy() == 0])
        res[h].append((d, top - bot))
for h, v in res.items():
    s = pd.Series(dict(v)).dropna()
    t_stat = s.mean() / s.std() * np.sqrt(len(s) / h)  # overlap-adjusted for multi-day horizons
    by_year = s.groupby(s.index.year).mean() * 1e4
    print(f"{h:>2}-day: mean spread {s.mean() * 1e4:+6.1f} bps, t={t_stat:+.1f}, positive days {np.mean(s > 0):.0%} | by year (bps): "
          + " ".join(f"{y % 100}:{v:+.0f}" for y, v in by_year.items()))


# ---- Tests 2 & 3: category rotation ----
def category_members(kind):
    if kind == "sector":
        m = pickle.load(open(DATA + "research_sector_map.pkl", "rb"))
        return {c: [s for s in px.columns if m.get(s) == c] for c in {v for v in m.values() if v}}
    im = pickle.load(open(DATA + "research_industry_map.pkl", "rb"))
    groups = {}
    for s, (_, ind) in im.items():
        if ind and s in px.columns:
            groups.setdefault(ind, []).append(s)
    return {k: v for k, v in groups.items() if len(v) >= 8}


def rotation(cat_score, cat_count, cat_ret, step, label):
    """Hold top 3 categories (>= 5 articles in the score window), equal weight, rebalanced every
    `step` days. Returns (strategy, baseline) daily return series after costs."""
    score_w = cat_score.rolling(step, min_periods=1).sum() / cat_count.rolling(step, min_periods=1).sum().replace(0, np.nan)
    count_w = cat_count.rolling(step, min_periods=1).sum()
    w = pd.DataFrame(0.0, index=cat_ret.index, columns=cat_ret.columns)
    held = pd.Series(0.0, index=cat_ret.columns)
    for i, d in enumerate(cat_ret.index):
        if i % step == 0:
            ok = score_w.loc[d][count_w.loc[d] >= 5].dropna()
            held = pd.Series(0.0, index=cat_ret.columns)
            if len(ok) >= 3:
                held[ok.nlargest(3).index] = 1 / 3
        w.loc[d] = held
    gross = (w * cat_ret.fillna(0)).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1).fillna(0)
    strat = gross - turnover * COST
    strat[w.sum(axis=1) == 0] = 0.0  # no signal: cash
    base = cat_ret.mean(axis=1)
    return strat, base


def report(label, strat, base, strat_gross=None):
    ok = True
    parts = []
    for a, b in HALVES:
        m = (strat.index >= a) & (strat.index < b)
        cs, cb, cy = stats(strat[m])[0], stats(base[m])[0], stats(spy_next[m])[0]
        ok &= cs > cb and cs > cy
        parts.append(f"{a[:4]}-{int(b[:4]) - 1}: {cs:+6.1%} vs base {cb:+6.1%} vs SPY {cy:+6.1%}")
    c, dd, sh = stats(strat)
    print(f"{label:34s} {' | '.join(parts)} | all: {c:+.1%} DD {dd:.0%} Sh {sh:.2f} -> {'PASS' if ok else 'no edge'}")


kinds = {}
for kind in ("sector", "industry"):
    cm = category_members(kind)
    sym2cat = {s: c for c, ss in cm.items() for s in ss}
    lc = long.assign(cat=long["sym"].map(sym2cat)).dropna(subset=["cat"])
    kinds[kind] = (lc.pivot_table(index="day", columns="cat", values="score", aggfunc="sum").reindex(days).fillna(0),
                   lc.pivot_table(index="day", columns="cat", values="score", aggfunc="count").reindex(days).fillna(0),
                   pd.DataFrame({c: ret_next[ss].mean(axis=1) for c, ss in cm.items()}))
# themes: articles matching the keyword; the theme's holdings that day = symbols tagged on them
th_score, th_count, th_ret = {}, {}, {}
for name, pat in THEMES.items():
    a = art[art["headline"].str.contains(pat, flags=re.I, regex=True, na=False)]
    th_score[name] = a.groupby("day")["score"].sum()
    th_count[name] = a.groupby("day")["score"].count()
    la = long[long["id"].isin(a["id"])]
    r = {d: np.nanmean(ret_next.loc[d, g["sym"].unique()]) for d, g in la.groupby("day") if d in ret_next.index}
    th_ret[name] = pd.Series(r)
kinds["theme"] = tuple(pd.DataFrame(x).reindex(days).fillna(0 if i < 2 else np.nan)
                       for i, x in enumerate((th_score, th_count, th_ret)))

for step, title in ((1, "TEST 2: daily rotation"), (5, "TEST 3: weekly rotation")):
    print(f"\n=== {title} -- top 3 by news score, after 2 bps/side ===")
    for kind, (sc, cn, cr) in kinds.items():
        if kind == "theme" and step > 1:
            print("themes: daily only (a theme's holdings are that day's tagged stocks)")
            continue
        strat, base = rotation(sc, cn, cr, step, kind)
        report(f"{kind}s ({cr.shape[1]} categories)", strat, base)
