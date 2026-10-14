"""When does the launch-day move happen? For each launch headline (same events as product_events.py),
pull that day's 1-minute bars (SIP) for the stock and SPY. t0 = headline minute if published during
market hours, else the 9:30 open (pre-market / previous evening news). Abnormal = stock minus SPY.
  done(k)   = move from the prior close to t0+k minutes   (already gone before you can act)
  left(k)   = move from t0+k to the day's close, and to the next day's close  (what you'd capture)
Round-trip cost 4 bps (2 bps/side, same as every earlier test)."""
import json
import pickle
import re
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "C:/OpenMind")
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from cerebral.trading.broker import _get_alpaca_credentials

exec(open("product_events.py", encoding="utf-8").read().split("A = abn.to_numpy()")[0].replace('print("sample headlines:")', "pass").replace('    print("  -", h[:100])', "    pass"))
ev = ev.assign(ts=pd.to_datetime(ev["created_at"], utc=True).dt.tz_convert("America/New_York"), day=days[ev["i0"].to_numpy()])
client = StockHistoricalDataClient(*_get_alpaca_credentials("paper"))
NY = "America/New_York"
KS = (0, 1, 5, 15, 30, 60)
COST = 0.0004
daily_close = px  # adjusted daily closes, for the prior close and the next day's close
spy_close = spy.reindex(days)

rows = []
for n, (day, g) in enumerate(ev.groupby("day")):
    syms = sorted(set(g["sym"]) | {"SPY"})
    start = pd.Timestamp(day).tz_localize(NY) + pd.Timedelta(hours=9, minutes=30)
    for attempt in range(5):
        try:
            bars = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=syms, timeframe=TimeFrame.Minute,
                                                          start=start, end=start + pd.Timedelta(hours=6, minutes=30), feed=DataFeed.SIP)).df
            break
        except Exception as e:
            time.sleep(10 * (attempt + 1))
    else:
        continue
    if bars.empty or "SPY" not in bars.index.get_level_values(0):
        continue
    close = bars["close"].unstack(0)
    close.index = close.index.tz_convert(NY)
    close = close.ffill()
    i0 = days.get_loc(day)
    for r in g.itertuples():
        if r.sym not in close.columns or i0 + 1 >= len(days):
            continue
        s, m = close[r.sym], close["SPY"]
        t0 = max(r.ts.floor("min"), start) if r.ts.date() == day.date() and r.ts.hour < 16 else start
        # daily closes are split/dividend-adjusted; minute bars are raw -> anchor with the day's raw close
        raw_close_s, raw_close_m = s.iloc[-1], m.iloc[-1]
        adj = lambda a, b: a / b  # ratio helper
        prev_s = daily_close[r.sym].iloc[i0 - 1] / daily_close[r.sym].iloc[i0] * raw_close_s
        prev_m = spy_close.iloc[i0 - 1] / spy_close.iloc[i0] * raw_close_m
        next_ratio_s = daily_close[r.sym].iloc[i0 + 1] / daily_close[r.sym].iloc[i0]
        next_ratio_m = spy_close.iloc[i0 + 1] / spy_close.iloc[i0]
        row = {"sym": r.sym, "year": day.year, "intraday": t0 > start,
               "day_total": (raw_close_s / prev_s - 1) - (raw_close_m / prev_m - 1)}
        for k in KS:
            t = t0 + pd.Timedelta(minutes=k)
            if t >= start + pd.Timedelta(hours=6, minutes=30):
                continue
            ps, pm = s.asof(t), m.asof(t)
            if np.isnan(ps) or np.isnan(pm):
                continue
            row[f"done_{k}"] = (ps / prev_s - 1) - (pm / prev_m - 1)
            row[f"left_close_{k}"] = (raw_close_s / ps - 1) - (raw_close_m / pm - 1)
            row[f"left_next_{k}"] = (raw_close_s * next_ratio_s / ps - 1) - (raw_close_m * next_ratio_m / pm - 1)
        rows.append(row)
    if n % 200 == 0:
        print(f"{n} days processed, {len(rows)} events", flush=True)

df = pd.DataFrame(rows)
df.to_pickle("launch_intraday.pkl")
t = lambda x: x.mean() / x.std() * np.sqrt(x.count())
print(f"\nevents measured: {len(df):,} ({df['intraday'].sum():,} published during market hours)")
for label, sub in (("ALL", df), ("published DURING market hours", df[df["intraday"]]), ("published BEFORE the open", df[~df["intraday"]])):
    print(f"\n=== {label} (n={len(sub):,}) -- day's total move vs SPY: mean {sub['day_total'].mean():+.2%} ===")
    print(f"{'act at':>12s} {'already done':>12s} {'left to close':>14s} {'net of cost':>11s} {'t':>5s} {'left to next close':>19s} {'net':>7s} {'t':>5s}")
    for k in KS:
        if f"done_{k}" not in sub or sub[f"done_{k}"].count() < 30:
            continue
        lc, ln = sub[f"left_close_{k}"], sub[f"left_next_{k}"]
        print(f"{'t0+' + str(k) + 'm':>12s} {sub[f'done_{k}'].mean():+12.2%} {lc.mean():+14.2%} {lc.mean() - COST:+11.2%} {t(lc):+5.1f} "
              f"{ln.mean():+19.2%} {ln.mean() - COST:+7.2%} {t(ln):+5.1f}")
intra = df[df["intraday"]]
if len(intra) and "left_close_5" in intra:
    by = intra.groupby("year")["left_close_5"].mean()
    print("\nduring-hours headlines, buy 5 min after, hold to close, by year: " + " ".join(f"{y % 100}:{v:+.2%}" for y, v in by.items()))
