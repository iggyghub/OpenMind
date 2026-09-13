"""News cache for historical replay (RP8).

Uses the same `bars.db` as `bar_cache.py`. Fetches via Alpaca News API
with manual pagination, dedupes on `article_id`, and implements gap-fill.
"""
import os
import sqlite3
from datetime import datetime

from cerebral.paths import data_dir
from cerebral.trading.broker import AlpacaMarketDataClient

NEWS_TABLE = """
CREATE TABLE IF NOT EXISTS news (
    article_id TEXT PRIMARY KEY,
    symbol TEXT,
    published_day TEXT,
    n_symbols INTEGER
)
"""

# ponytail: heuristic prominence filter; revisit if too noisy or too strict
MAX_SYMBOLS_PROMINENCE = 3

def init_news_db(db_path: str = None) -> None:
    """Ensures the `news` table exists in the given database."""
    if db_path is None:
        db_path = os.path.join(data_dir(), "bars.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(NEWS_TABLE)
        conn.commit()
    finally:
        conn.close()

def _get_cached_max_day(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(published_day) FROM news WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row[0] else None

def fetch_news(symbol: str, start: str, end: str, max_symbols: int = MAX_SYMBOLS_PROMINENCE, client: object = None) -> list[dict]:
    """Fetch historical news for `symbol` over [start, end].

    Implements manual pagination using `next_page_token`. Dedupes on
    `article_id`. Only fetches the gap between the cached max day and `end`.
    Returns a list of dicts: `{"article_id": str, "symbols": list[str], "published_at": str}`.
    """
    db_path = os.path.join(data_dir(), "bars.db")
    conn = sqlite3.connect(db_path)
    try:
        max_cached_day = _get_cached_max_day(conn, symbol)
        if max_cached_day is not None and max_cached_day >= end:
            return []

        fetch_start = start
        if max_cached_day is not None:
            d = datetime.fromisoformat(max_cached_day).date() + datetime.timedelta(days=1)
            fetch_start = d.isoformat()

        news_client = client or AlpacaMarketDataClient("paper")
        next_token = None
        new_articles = []

        while True:
            resp = news_client.get_news(symbol=symbol, start=fetch_start, end=end, limit=50, page_token=next_token)
            articles = resp.get("articles", []) if isinstance(resp, dict) else getattr(resp, "articles", [])
            if not articles:
                break

            for a in articles:
                aid = a.get("id") or a.get("article_id")
                if aid not in {x.get("id") or x.get("article_id") for x in new_articles}:
                    new_articles.append(a)

            next_token = resp.get("next_page_token") if isinstance(resp, dict) else getattr(resp, "next_page_token", None)
            if not next_token:
                break

        for a in new_articles:
            aid = a.get("id") or a.get("article_id")
            symbols = a.get("symbols", [])
            pub_day = (a.get("published_at") or a.get("published_date", "") or "").split("T")[0]
            n_sym = len(symbols)
            for sym_tag in symbols:
                conn.execute(
                    "INSERT OR IGNORE INTO news (article_id, symbol, published_day, n_symbols) VALUES (?, ?, ?, ?)",
                    (aid, sym_tag, pub_day, n_sym)
                )
        conn.commit()
        return new_articles
    finally:
        conn.close()

def count_news_events(symbol: str, day: str, max_symbols: int = MAX_SYMBOLS_PROMINENCE) -> int:
    """Counts rows for that `(symbol, day)` with `n_symbols <= max_symbols`."""
    db_path = os.path.join(data_dir(), "bars.db")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM news WHERE symbol = ? AND published_day = ? AND n_symbols <= ?",
            (symbol, day, max_symbols)
        ).fetchone()
        return row[0]
    finally:
        conn.close()
