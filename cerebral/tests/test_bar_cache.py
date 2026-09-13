import os
import sqlite3
import pandas as pd
import pytest
from unittest.mock import patch

from cerebral.trading.bar_cache import get_bars


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
