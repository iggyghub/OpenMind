"""Pull every Benzinga headline (via Alpaca's news API, raw REST -- the SDK drops next_page_token)
from 2016-01-01 to yesterday into cerebral/data/news_history.db. Resumable: a day is recorded in
days_done only after all its pages are stored, so a crash or restart just redoes that day."""
import json
import sqlite3
import sys
import time
from datetime import date, timedelta

import requests

sys.path.insert(0, r"C:\OpenMind")
from cerebral.trading.broker import _get_alpaca_credentials

DB = r"C:\OpenMind\cerebral\data\news_history.db"
URL = "https://data.alpaca.markets/v1beta1/news"
k, s = _get_alpaca_credentials("paper")
H = {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}

con = sqlite3.connect(DB)
con.execute("CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, created_at TEXT, headline TEXT, summary TEXT, symbols TEXT)")
con.execute("CREATE TABLE IF NOT EXISTS days_done (day TEXT PRIMARY KEY, n INTEGER)")
done = {r[0] for r in con.execute("SELECT day FROM days_done")}


def get(params):
    for attempt in range(8):
        try:
            r = requests.get(URL, headers=H, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print("retry after error:", e, flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"gave up on {params}")


day, end = date(2016, 1, 1), date.today() - timedelta(days=1)
t0, total = time.time(), 0
while day <= end:
    d = day.isoformat()
    day += timedelta(days=1)
    if d in done:
        continue
    rows, tok = [], None
    while True:
        p = {"start": d + "T00:00:00Z", "end": d + "T23:59:59Z", "limit": 50, "sort": "asc"}
        if tok:
            p["page_token"] = tok
        j = get(p)
        for a in j.get("news", []):
            rows.append((str(a["id"]), a["created_at"], a.get("headline", ""), a.get("summary", ""), json.dumps(a.get("symbols", []))))
        tok = j.get("next_page_token")
        time.sleep(0.3)  # stay under 200 requests/minute
        if not tok:
            break
    con.executemany("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO days_done VALUES (?,?)", (d, len(rows)))
    con.commit()
    total += len(rows)
    if d.endswith("-01"):
        print(f"{d}: {total} articles so far, {(time.time() - t0) / 60:.0f} min", flush=True)
print("DONE", total, "articles", flush=True)
