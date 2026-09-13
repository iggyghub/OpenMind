"""SQLite historical bar cache (RP3, REPLAY.md).

Replaces the old per-call CSV file cache in cerebral/trading_data.py, which
had two live bugs: it was write-only on the Alpaca path (returns before its
own TTL read block is ever reached) and its cache key included the exact
date range, so a sliding today-N..today window accumulated near-duplicate
files daily instead of ever being reused (confirmed live: 1533 files for
~20 symbols). This keys on (symbol, interval, ts) instead, so any two
fetches for the same symbol/interval share one growing table and a range
query is a plain `WHERE ts BETWEEN ? AND ?`.

Historical bars before today never change, so there is no cache invalidation
policy beyond "fetch whatever's missing" -- gap-fill on read.

Module-level `AlpacaMarketDataClient` import (not lazy) is deliberate: it
must be a persistent attribute on THIS module so tests can
`unittest.mock.patch("cerebral.trading.bar_cache.AlpacaMarketDataClient")`
the way cerebral/tests/test_bar_cache.py does -- patching the broker module's
own attribute would not affect an already-lazily-imported reference here.

`data_dir` stays a LAZY import inside `get_bars`, for the opposite reason:
existing tests (this file's own `mock_data_dir` fixture, and
cerebral/tests/test_trading_data.py's callers) patch
`cerebral.paths.data_dir` itself (the source), which only take effect on a
fresh per-call `from cerebral.paths import data_dir` -- a module-level
import here would bind the real function once at import time and silently
ignore the patch, writing test data into the real
`cerebral/data/bars.db` instead of the test's tmp dir.
"""
import os
import sqlite3
from datetime import datetime

import pandas as pd

from cerebral.trading.broker import AlpacaMarketDataClient

_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _query_range(conn: sqlite3.Connection, symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Cached rows in [start, end], shaped to fetch_ohlcv's documented
    contract: ascending DatetimeIndex named "Date", capitalised OHLCV
    columns. A real `ts`-indexed read (not a bare column) -- read_sql_query
    with no index_col returns a meaningless positional RangeIndex, which a
    prior version of this function then mistakenly tried to read back out
    as a "Date" *column* (KeyError, since "Date" was only ever an index
    name, and the source "ts" column had already been dropped)."""
    df = pd.read_sql_query(
        "SELECT ts, open, high, low, close, volume FROM bars "
        "WHERE symbol = ? AND interval = ? AND ts >= ? AND ts <= ? ORDER BY ts",
        conn, params=(symbol, interval, start, end), parse_dates=["ts"],
    )
    df = df.set_index("ts")
    df.index.name = "Date"
    df.columns = _COLUMNS
    return df


def get_bars(symbol: str, start: str, end: str, interval: str = "1d", refresh: bool = False) -> pd.DataFrame:
    """Cached historical bars for `symbol`/`interval` over [start, end].

    Fetches only the gap between what's cached and `end` (or the full range
    if nothing is cached yet). `refresh=True` forces a full re-fetch of
    [start, end] regardless of what's cached -- the escape hatch for the one
    known ceiling this cache can't handle on its own: a split occurring
    AFTER a fetch retroactively rewrites every earlier adjusted price (RP0
    already fetches Adjustment.ALL, but a NEW split after the fact still
    needs a manual refresh). Corporate actions are rare enough that a manual
    flag beats polling a corporate-actions endpoint for this.
    # ponytail: no automatic split-detection -- refresh=True by hand (or a
    # future scheduled job) until a real need for the automatic version shows up.
    """
    from cerebral.paths import data_dir

    db_path = os.path.join(data_dir(), "bars.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT,
                interval TEXT,
                ts TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                fetched_at TEXT,
                PRIMARY KEY (symbol, interval, ts)
            )
            """
        )
        conn.commit()

        df = _query_range(conn, symbol, interval, start, end)
        max_cached_ts = df.index.max() if not df.empty else None

        needs_refresh = refresh
        if not needs_refresh:
            needs_refresh = max_cached_ts is None or pd.isnull(max_cached_ts) or pd.to_datetime(end) > max_cached_ts

        if not needs_refresh:
            return df

        # Only narrow the fetch to the gap for an actual gap-fill -- a
        # refresh=True request means "re-fetch [start, end] in full",
        # not "fetch from wherever the cache already reaches".
        fetch_start = start
        if not refresh and max_cached_ts is not None and not pd.isnull(max_cached_ts):
            fetch_start = max_cached_ts.strftime("%Y-%m-%d")

        new_df = AlpacaMarketDataClient("paper").get_bars(symbol, fetch_start, end, interval)
        if not new_df.empty:
            fetched_at = datetime.now().isoformat()
            records = [
                (
                    symbol, interval, ts.strftime("%Y-%m-%d"),
                    row["Open"], row["High"], row["Low"], row["Close"], row["Volume"],
                    fetched_at,
                )
                for ts, row in new_df.iterrows()
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO bars "
                "(symbol, interval, ts, open, high, low, close, volume, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                records,
            )
            conn.commit()

        return _query_range(conn, symbol, interval, start, end)
    finally:
        conn.close()
