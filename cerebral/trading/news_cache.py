"""News cache for historical replay (RP8).

Uses the same `bars.db` as `bar_cache.py`. Fetches via Alpaca's real News
API (`alpaca.data.historical.news.NewsClient`) with manual pagination
(unlike bars, the SDK does not auto-paginate news), dedupes on
`article_id`, and gap-fills on refetch the same way `bar_cache.get_bars`
does. No LLM anywhere in this module (REPLAY.md D4) -- news_event_count is
a per-day article count filtered by a prominence threshold, not sentiment.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

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


def _resolve_db_path(db_path: "str | None") -> str:
    if db_path is not None:
        return db_path
    from cerebral.paths import data_dir  # lazy: tests patch cerebral.paths.data_dir
    return os.path.join(data_dir(), "bars.db")


def init_news_db(db_path: "str | None" = None) -> str:
    """Ensures the `news` table exists in the given (or default) database.
    Returns the resolved db_path so callers that didn't pass one can reuse it."""
    db_path = _resolve_db_path(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(NEWS_TABLE)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _get_cached_max_day(conn: sqlite3.Connection, symbol: str) -> "str | None":
    row = conn.execute(
        "SELECT MAX(published_day) FROM news WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row and row[0] else None


def fetch_news(
    symbol: str, start: str, end: str,
    max_symbols: int = MAX_SYMBOLS_PROMINENCE,
    client: object = None, db_path: "str | None" = None,
) -> list:
    """Fetch historical news for `symbol` over [start, end], caching into
    the news table. Implements manual pagination via next_page_token --
    unlike bars, Alpaca's News API does not auto-paginate. Dedupes on
    article id. Only fetches the gap between the cached max day and `end`
    (same gap-fill convention as bar_cache.get_bars).

    Returns the list of newly-fetched article objects (real News model
    instances in production, or whatever the injected `client` returns in
    tests) -- callers needing counts should use count_news_events instead
    of the length of this list, since already-cached days aren't refetched.
    """
    db_path = init_news_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        max_cached_day = _get_cached_max_day(conn, symbol)
        if max_cached_day is not None and max_cached_day >= end:
            return []

        fetch_start = start
        if max_cached_day is not None:
            d = datetime.fromisoformat(max_cached_day).date() + timedelta(days=1)
            fetch_start = d.isoformat()

        if client is None:
            from alpaca.data.historical.news import NewsClient
            from cerebral.trading.broker import _get_alpaca_credentials
            api_key, api_secret = _get_alpaca_credentials("paper")
            client = NewsClient(api_key, api_secret)

        next_token = None
        new_articles = []
        seen_ids = set()

        while True:
            from alpaca.data.requests import NewsRequest
            req = NewsRequest(
                symbols=symbol, start=fetch_start, end=end,
                limit=50, page_token=next_token,
            )
            news_set = client.get_news(req)
            # Real NewsSet.data == {"news": [News, ...]}; tolerate a plain
            # dict-shaped fake in tests that skips the real Pydantic model.
            articles = news_set.data.get("news", []) if hasattr(news_set, "data") else []
            if not articles:
                break

            for a in articles:
                aid = a.id
                if aid not in seen_ids:
                    seen_ids.add(aid)
                    new_articles.append(a)

            next_token = getattr(news_set, "next_page_token", None)
            if not next_token:
                break

        for a in new_articles:
            article_id = str(a.id)
            created_at = a.created_at
            pub_day = created_at.date().isoformat() if hasattr(created_at, "date") else str(created_at).split("T")[0]
            n_sym = len(a.symbols)
            for sym_tag in a.symbols:
                conn.execute(
                    "INSERT OR IGNORE INTO news (article_id, symbol, published_day, n_symbols) VALUES (?, ?, ?, ?)",
                    (article_id, sym_tag, pub_day, n_sym),
                )
        conn.commit()
        return new_articles
    finally:
        conn.close()


def count_news_events(
    symbol: str, day: str, max_symbols: int = MAX_SYMBOLS_PROMINENCE,
    db_path: "str | None" = None,
) -> int:
    """Counts rows for that `(symbol, day)` with `n_symbols <= max_symbols`
    -- the prominence filter, dropping roundup-style articles tagged to
    many tickers at once."""
    db_path = init_news_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM news WHERE symbol = ? AND published_day = ? AND n_symbols <= ?",
            (symbol, day, max_symbols),
        ).fetchone()
        return row[0]
    finally:
        conn.close()
