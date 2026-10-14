import os
import sqlite3
import pandas as pd
import pytest
from unittest.mock import patch

from cerebral.trading.bar_cache import get_bars

_CREATE_BARS_TABLE = """
    CREATE TABLE IF NOT EXISTS bars (
        symbol TEXT, interval TEXT, ts TEXT,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        fetched_at TEXT, PRIMARY KEY (symbol, interval, ts)
    )
"""


@pytest.fixture
def mock_data_dir(tmp_path):
    """Override data_dir to use a temporary directory for test isolation."""
    with patch("cerebral.paths.data_dir", return_value=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def mock_alpaca_client():
    """Mock AlpacaMarketDataClient to avoid real network calls."""
    with patch("cerebral.trading.bar_cache.AlpacaMarketDataClient") as MockClient:
        mock_instance = MockClient.return_value
        yield mock_instance


def test_empty_db_fetches_and_caches(mock_data_dir, mock_alpaca_client):
    mock_alpaca_client.get_bars.return_value = pd.DataFrame({
        "Open": [100.0, 101.0],
        "High": [102.0, 103.0],
        "Low": [99.0, 100.0],
        "Close": [101.0, 102.0],
        "Volume": [1000, 1100]
    }, index=pd.to_datetime(["2023-01-01", "2023-01-02"]))

    df = get_bars("AAPL", "2023-01-01", "2023-01-02")
    
    # Verify it was fetched once
    assert mock_alpaca_client.get_bars.call_count == 1
    assert len(df) == 2
    assert df.index.name == "Date"
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]

    # Second call with same range should NOT invoke fetch again
    df2 = get_bars("AAPL", "2023-01-01", "2023-01-02")
    assert mock_alpaca_client.get_bars.call_count == 1
    pd.testing.assert_frame_equal(df, df2)


def test_wider_end_fetches_only_gap(mock_data_dir, mock_alpaca_client):
    # Pre-populate DB with 2 days
    db_path = os.path.join(str(mock_data_dir), "bars.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_BARS_TABLE)
    conn.executemany(
        "INSERT INTO bars (symbol, interval, ts, open, high, low, close, volume, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("AAPL", "1d", "2023-01-01", 100, 102, 99, 101, 1000, "2023-01-03T00:00:00"),
            ("AAPL", "1d", "2023-01-02", 101, 103, 100, 102, 1100, "2023-01-03T00:00:00"),
        ]
    )
    conn.commit()
    conn.close()
    
    mock_alpaca_client.get_bars.return_value = pd.DataFrame({
        "Open": [103.0],
        "High": [104.0],
        "Low": [102.0],
        "Close": [104.0],
        "Volume": [1200]
    }, index=pd.to_datetime(["2023-01-03"]))

    df = get_bars("AAPL", "2023-01-01", "2023-01-03")
    
    # Should have fetched only the gap
    mock_alpaca_client.get_bars.assert_called_once_with("AAPL", "2023-01-02", "2023-01-03", "1d")
    assert len(df) == 3


def test_refresh_true_fetches_full_range(mock_data_dir, mock_alpaca_client):
    # Pre-populate DB with 2 days
    db_path = os.path.join(str(mock_data_dir), "bars.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_BARS_TABLE)
    conn.executemany(
        "INSERT INTO bars (symbol, interval, ts, open, high, low, close, volume, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("AAPL", "1d", "2023-01-01", 100, 102, 99, 101, 1000, "2023-01-03T00:00:00"),
            ("AAPL", "1d", "2023-01-02", 101, 103, 100, 102, 1100, "2023-01-03T00:00:00"),
        ]
    )
    conn.commit()
    conn.close()
    
    mock_alpaca_client.get_bars.return_value = pd.DataFrame({
        "Open": [100.0, 101.0],
        "High": [102.0, 103.0],
        "Low": [99.0, 100.0],
        "Close": [101.0, 102.0],
        "Volume": [1000, 1100]
    }, index=pd.to_datetime(["2023-01-01", "2023-01-02"]))

    df = get_bars("AAPL", "2023-01-01", "2023-01-02", refresh=True)
    
    # Should have fetched the full range, not just the gap
    mock_alpaca_client.get_bars.assert_called_once_with("AAPL", "2023-01-01", "2023-01-02", "1d")
    assert len(df) == 2


def _intraday_frame():
    idx = pd.DatetimeIndex(
        ["2023-01-03 14:30", "2023-01-03 14:35", "2023-01-03 20:55"], tz="UTC"
    )  # 09:30, 09:35 and 15:55 New York
    return pd.DataFrame({"Open": [1.0, 2.0, 3.0], "High": [1.0, 2.0, 3.0], "Low": [1.0, 2.0, 3.0],
                         "Close": [1.0, 2.0, 3.0], "Volume": [10, 20, 30]}, index=idx)


def test_intraday_bars_keep_their_time_of_day(mock_data_dir, mock_alpaca_client):
    mock_alpaca_client.get_bars.return_value = _intraday_frame()
    df = get_bars("AAPL", "2023-01-03", "2023-01-03", "5m")
    assert len(df) == 3                                    # was collapsed to 1 bar per day
    assert [t.strftime("%H:%M") for t in df.index] == ["09:30", "09:35", "15:55"]
    # the bare end date covers the whole day for the read AND the fetch reaches into the next day
    assert mock_alpaca_client.get_bars.call_args.args[2] == "2023-01-04T00:00:00"
    assert len(get_bars("AAPL", "2023-01-03", "2023-01-03", "5m")) == 3
    assert mock_alpaca_client.get_bars.call_count == 1     # second read is served from cache


def test_collapsed_intraday_rows_are_purged_but_daily_rows_survive(mock_data_dir, mock_alpaca_client):
    conn = sqlite3.connect(os.path.join(str(mock_data_dir), "bars.db"))
    conn.execute(_CREATE_BARS_TABLE)
    conn.executemany(
        "INSERT INTO bars VALUES (?, ?, ?, 1, 1, 1, 1, 1, 'x')",
        [("AAPL", "5m", "2023-01-03"), ("AAPL", "1d", "2023-01-03")],
    )
    conn.commit()
    conn.close()
    mock_alpaca_client.get_bars.return_value = pd.DataFrame()
    assert len(get_bars("AAPL", "2023-01-03", "2023-01-03", "1d")) == 1
    get_bars("AAPL", "2023-01-03", "2023-01-03", "5m")
    conn = sqlite3.connect(os.path.join(str(mock_data_dir), "bars.db"))
    assert conn.execute("SELECT count(*) FROM bars WHERE interval='5m'").fetchone()[0] == 0
    conn.close()


def test_intraday_fetch_never_reaches_into_the_last_twenty_minutes(mock_data_dir, mock_alpaca_client):
    from datetime import datetime, timedelta, timezone
    mock_alpaca_client.get_bars.return_value = pd.DataFrame()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    get_bars("AAPL", today, today, "5m")
    fetched_to = datetime.fromisoformat(mock_alpaca_client.get_bars.call_args.args[2])
    assert fetched_to <= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=19)


def test_stray_old_fragment_does_not_hide_an_earlier_start(mock_data_dir, mock_alpaca_client):
    conn = sqlite3.connect(os.path.join(str(mock_data_dir), "bars.db"))
    conn.execute(_CREATE_BARS_TABLE)
    conn.execute("INSERT INTO bars VALUES ('AAPL', '1d', '2024-06-03', 1, 1, 1, 1, 1, 'x')")
    conn.commit()
    conn.close()
    mock_alpaca_client.get_bars.return_value = pd.DataFrame({
        "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
        index=pd.to_datetime(["2020-01-02"]))
    get_bars("AAPL", "2020-01-01", "2024-06-03")
    assert mock_alpaca_client.get_bars.call_args.args[1] == "2020-01-01"     # backfilled from the requested start
    get_bars("AAPL", "2020-01-01", "2024-06-03")
    assert mock_alpaca_client.get_bars.call_count == 1                        # and remembered: not asked again


def test_concurrent_cache_hits_do_not_contend_for_the_write_lock(mock_data_dir, mock_alpaca_client):
    from concurrent.futures import ThreadPoolExecutor
    mock_alpaca_client.get_bars.return_value = _intraday_frame()
    get_bars("AAPL", "2023-01-03", "2023-01-03", "5m")                 # warm the cache
    conn = sqlite3.connect(os.path.join(str(mock_data_dir), "bars.db"))
    conn.execute("BEGIN IMMEDIATE")                                    # someone else holds the write lock...
    try:
        with ThreadPoolExecutor(4) as pool:                            # ...pure cache hits must still read fine
            results = list(pool.map(lambda _: len(get_bars("AAPL", "2023-01-03", "2023-01-03", "5m")), range(8)))
    finally:
        conn.rollback()
        conn.close()
    assert results == [3] * 8
