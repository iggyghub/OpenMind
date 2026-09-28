"""News cache for historical replay (RP8).

Uses the same `bars.db` as `bar_cache.py`. Fetches via Alpaca's real News
API (`alpaca.data.historical.news.NewsClient`), letting the SDK paginate, dedupes on
`article_id`, and gap-fills on refetch the same way `bar_cache.get_bars`
does -- including `bar_cache`'s `covered_from`/backfill tracking (added
2026-09-23 after a real bug: gap-fill only ever reached FORWARD from the
newest cached day, so a symbol with only a small recent fragment cached
from unrelated ad-hoc use silently refused to backfill an older requested
start date -- same bug class bar_cache.py fixed as issue #1335). No LLM
anywhere in this module (REPLAY.md D4) -- news_event_count is a per-day
article count filtered by a prominence threshold, not sentiment.
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

# Tracks the earliest date ever actually requested per symbol, the same way bar_cache.py's
# `bars_cover` table does -- distinct from MAX(published_day) in `news`, which only tells you
# the newest cached day, not whether an OLDER start date was ever actually asked for.
NEWS_COVER_TABLE = """
CREATE TABLE IF NOT EXISTS news_cover (
    symbol TEXT PRIMARY KEY,
    covered_from TEXT
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
    """Ensures the `news`/`news_cover` tables exist in the given (or default) database.
    Returns the resolved db_path so callers that didn't pass one can reuse it."""
    db_path = _resolve_db_path(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # One-time invalidation: every row cached before the 2026-09-25 pagination fix is
        # truncated to 50 articles per fetch, yet news_cover marks it complete. Wipe once so
        # the next fetch_news per symbol re-backfills in full.
        fresh = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'news_paging_fixed'"
        ).fetchone() is None
        conn.execute(NEWS_TABLE)
        conn.execute(NEWS_COVER_TABLE)
        if fresh:
            conn.execute("DELETE FROM news")
            conn.execute("DELETE FROM news_cover")
            conn.execute("CREATE TABLE news_paging_fixed (x)")
        conn.commit()
    finally:
        conn.close()
    return db_path


def _get_cached_max_day(conn: sqlite3.Connection, symbol: str) -> "str | None":
    row = conn.execute(
        "SELECT MAX(published_day) FROM news WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row and row[0] else None


def _get_covered_from(conn: sqlite3.Connection, symbol: str) -> "str | None":
    row = conn.execute(
        "SELECT covered_from FROM news_cover WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row is not None:
        return row[0]
    # Legacy cache (rows already present before news_cover existed): trust the oldest
    # cached day as a lower bound, same fallback bar_cache.py's get_bars uses.
    oldest = conn.execute(
        "SELECT MIN(published_day) FROM news WHERE symbol = ?", (symbol,)
    ).fetchone()[0]
    return oldest if oldest else None


def fetch_news(
    symbol: str, start: str, end: str,
    max_symbols: int = MAX_SYMBOLS_PROMINENCE,
    client: object = None, db_path: "str | None" = None,
) -> list:
    """Fetch historical news for `symbol` over [start, end], caching into
    the news table. Pagination is the SDK's own (no `limit` -- see the
    comment at the call). Dedupes on article id. Only fetches the gap between the cached max day and `end`
    (same gap-fill convention as bar_cache.get_bars).

    Returns the list of newly-fetched article objects (real News model
    instances in production, or whatever the injected `client` returns in
    tests) -- callers needing counts should use count_news_events instead
    of the length of this list, since already-cached days aren't refetched.

    Backfill-aware (2026-09-23): a `start` older than what was previously ever requested for
    this symbol triggers a real fetch even if a newer fragment is already cached -- gap-fill on
    its own only ever reaches forward from the newest cached day, which would otherwise silently
    refuse to backfill older history for a symbol that already has ANY recent cached news.
    """
    db_path = init_news_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        covered_from = _get_covered_from(conn, symbol)
        needs_backfill = covered_from is not None and start < covered_from

        max_cached_day = _get_cached_max_day(conn, symbol)
        needs_refresh = needs_backfill or max_cached_day is None or max_cached_day < end
        if not needs_refresh:
            return []

        # Only narrow the fetch to the gap for a genuine forward gap-fill -- a backfill request
        # (start predates covered_from) re-fetches [start, end] in full rather than guessing a
        # narrower backfill-only sub-range; INSERT OR IGNORE on article_id makes the overlap with
        # already-cached days harmless.
        fetch_start = start
        if not needs_backfill and max_cached_day is not None:
            d = datetime.fromisoformat(max_cached_day).date() + timedelta(days=1)
            fetch_start = d.isoformat()

        if client is None:
            from alpaca.data.historical.news import NewsClient
            from cerebral.trading.broker import _get_alpaca_credentials
            api_key, api_secret = _get_alpaca_credentials("paper")
            client = NewsClient(api_key, api_secret)

        # No `limit`: alpaca-py's NewsRequest.limit is a TOTAL cap, not a page size -- the SDK
        # pages internally (and swallows next_page_token, so NewsSet never exposes it). The old
        # limit=50 + manual next_page_token loop silently stopped at 50 articles per fetch
        # (verified 2026-09-25: 50 vs 662 for 2016-03-15). Omitting it pages to exhaustion.
        from alpaca.data.requests import NewsRequest
        news_set = client.get_news(NewsRequest(symbols=symbol, start=fetch_start, end=end))
        # Real NewsSet.data == {"news": [News, ...]}; tolerate a plain
        # dict-shaped fake in tests that skips the real Pydantic model.
        articles = news_set.data.get("news", []) if hasattr(news_set, "data") else []
        new_articles = list({a.id: a for a in articles}.values())  # dedupe on article id

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

        # Record coverage even when new_articles is empty -- [start, ...) was still actually
        # asked for, so it must not be re-requested from Alpaca on every future call just
        # because no news happened to exist in that range (same convention bar_cache.get_bars
        # uses for its own bars_cover table).
        new_covered_from = min(start, covered_from) if covered_from else start
        conn.execute(
            "INSERT OR REPLACE INTO news_cover (symbol, covered_from) VALUES (?, ?)",
            (symbol, new_covered_from),
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
