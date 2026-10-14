"""Q2: do stocks rise around product launches, how often, how much? Event study on Benzinga
headlines 2016-2026 tagged to one S&P 500 member (single-symbol articles only, so the product is
that company's). Returns are vs SPY (abnormal). Day 0 = the trading day the news is known by
(before 16:00 ET that day, else next day). Tradeable part = after day 0's close."""
import json
import pickle
import re
import sqlite3

import numpy as np
import pandas as pd

DATA = "C:/OpenMind/cerebral/data/"
LAUNCH = re.compile(r"\b(launch(es|ed|ing)?|unveil(s|ed|ing)?|introduc(es|ed|ing)|debut(s|ed)?|rolls? out|releas(es|ed) (new|its)|announces new|new (product|model|chip|phone|iphone|platform|service|drug|vehicle))\b", re.I)
NOISE = re.compile(r"\b(analyst|price target|downgrade|upgrade|options|shares of|stock|ETF|fund|IPO|offering|launches? (a |an )?(tender|offer|review|probe|investigation|strategic review|buyback|share repurchase))\b", re.I)

closes = pickle.load(open(DATA + "research_sp500_close.pkl", "rb"))
px = pd.DataFrame(closes).sort_index()
px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
px = px[~px.index.duplicated()]
px = px[px.index >= "2015-06-01"]
days = px.index
spy = __import__("yfinance").download("SPY", start="2015-06-01", auto_adjust=True, progress=False)["Close"].squeeze()
spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
abn = px.pct_change(fill_method=None).sub(spy.pct_change().reindex(days), axis=0)  # daily return vs SPY
sector = pickle.load(open(DATA + "research_sector_map.pkl", "rb"))

con = sqlite3.connect(DATA + "news_history.db")
art = pd.read_sql("SELECT created_at, headline, symbols FROM news", con)
art = art[art["headline"].str.contains(LAUNCH, na=False) & ~art["headline"].str.contains(NOISE, na=False)]
art["syms"] = art["symbols"].map(json.loads)
art = art[art["syms"].map(len) == 1]
art["sym"] = art["syms"].str[0]
art = art[art["sym"].isin(px.columns)]
t = pd.to_datetime(art["created_at"], utc=True).dt.tz_convert("America/New_York")
cal = t.dt.tz_localize(None).dt.normalize() + pd.to_timedelta((t.dt.hour >= 16).astype(int), unit="D")
art["i0"] = np.searchsorted(days.values, cal.values.astype("datetime64[ns]"))
art = art[(art["i0"] >= 5) & (art["i0"] < len(days) - 21)].sort_values("i0")
# one event per stock per 20 trading days (a launch often gets several headlines)
keep, last = [], {}
for idx, r in art.iterrows():
    if r["i0"] - last.get(r["sym"], -99) > 20:
        keep.append(idx)
        last[r["sym"]] = r["i0"]
ev = art.loc[keep]
print(f"launch events: {len(ev):,} across {ev['sym'].nunique()} companies, {len(ev) / 10.7:.0f} per year")
print("sample headlines:")
for h in ev.sample(6, random_state=1)["headline"]:
    print("  -", h[:100])

A = abn.to_numpy()
col = {c: i for i, c in enumerate(px.columns)}
WINDOWS = {"before (days -5..-1)": (-5, -1), "day of (0)": (0, 0), "next 5 days (+1..+5)": (1, 5), "next 20 days (+1..+20)": (1, 20)}
res = {w: [] for w in WINDOWS}
for _, r in ev.iterrows():
    j = col[r["sym"]]
    for w, (a, b) in WINDOWS.items():
        seg = A[r["i0"] + a: r["i0"] + b + 1, j]
        if not np.isnan(seg).any():
            res[w].append(np.prod(1 + seg) - 1)
print(f"\n{'window (vs SPY)':24s} {'mean':>7s} {'median':>7s} {'% positive':>10s} {'t':>6s}")
for w, v in res.items():
    v = np.array(v)
    print(f"{w:24s} {v.mean():+7.2%} {np.median(v):+7.2%} {np.mean(v > 0):10.0%} {v.mean() / v.std() * np.sqrt(len(v)):+6.1f}")

ev = ev.assign(sec=ev["sym"].map(sector), post=[np.prod(1 + A[r.i0 + 1: r.i0 + 6, col[r.sym]]) - 1 for r in ev.itertuples()])
print("\nnext 5 days (+1..+5) vs SPY, by sector (sectors with >= 100 events):")
for s, g in ev.dropna(subset=["post"]).groupby("sec"):
    if len(g) >= 100:
        print(f"  {s:24s} n={len(g):5d}  mean {g['post'].mean():+.2%}  % positive {np.mean(g['post'] > 0):.0%}  t={g['post'].mean() / g['post'].std() * np.sqrt(len(g)):+.1f}")
ev2 = ev.dropna(subset=["post"])
by_year = ev2.groupby(days[ev2["i0"].to_numpy()].year.to_numpy())["post"].mean()
print("\nnext 5 days by year: " + " ".join(f"{y % 100}:{v:+.2%}" for y, v in by_year.items()))
