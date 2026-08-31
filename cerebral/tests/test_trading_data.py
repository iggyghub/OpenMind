"""Unit tests for the trading_data module.

All tests use fixture DataFrames and mocked yfinance calls —
no real network requests are made.
"""

import os
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yfinance as yf
from yfinance.exceptions import YFException

from cerebral import trading_data


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Redirect the module's cache dir to a per-test tmp dir.

    Without this, tests sharing a symbol/date-range key (several use AAPL /
    2023-01-01 / 2023-01-10) silently read each other's cached CSV instead of
    exercising their own mocked scenario.
    """
    monkeypatch.setattr(trading_data, "_CACHE_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def no_alpaca_market_data(monkeypatch):
    """fetch_ohlcv tries AlpacaMarketDataClient first, falling back to
    yfinance only on exception -- these tests are specifically exercising
    the yfinance path (mocking yf.Ticker), so once real Alpaca paper
    credentials exist in keyring, every test here silently started making
    a real network call to Alpaca instead of ever touching the mock. Force
    the Alpaca branch to fail so it falls through, restoring the "no real
    network requests" contract in this file's own module docstring."""
    class _AlwaysFails:
        def __init__(self, *a, **kw):
            raise RuntimeError("Alpaca disabled in trading_data tests")
    monkeypatch.setattr("cerebral.trading.broker.AlpacaMarketDataClient", _AlwaysFails)


@pytest.fixture
def mock_ohlcv_df():
    """Return a realistic OHLCV DataFrame for testing."""
    dates = pd.date_range(start="2023-01-01", end="2023-01-10", freq="D")
    return pd.DataFrame(
        {
            "Open": [100 + i for i in range(len(dates))],
            "High": [105 + i for i in range(len(dates))],
            "Low": [95 + i for i in range(len(dates))],
            "Close": [102 + i for i in range(len(dates))],
            "Volume": [1000 * (i + 1) for i in range(len(dates))],
        },
        index=dates,
    )


# ── Structure & content tests ──────────────────────────────────────


def test_fetch_ohlcv_returns_dataframe(mock_ohlcv_df):
    """fetch_ohlcv returns a DataFrame with the expected columns and index."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = mock_ohlcv_df

        result = trading_data.fetch_ohlcv("AAPL", "2023-01-01", "2023-01-10")

        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert result.index.name == "Date"
        assert len(result) == 10


def test_fetch_ohlcv_strips_non_ohlvc_columns(mock_ohlcv_df):
    """Extra yfinance columns (Dividends, Stock Splits) are not returned."""
    full_df = mock_ohlcv_df.copy()
    full_df["Dividends"] = 0.0
    full_df["Stock Splits"] = 1.0

    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = full_df

        result = trading_data.fetch_ohlcv("TSLA", "2023-01-01", "2023-01-10")

        assert set(result.columns) == {"Open", "High", "Low", "Close", "Volume"}


# ── Caching tests ─────────────────────────────────────────────────


def test_fetch_ohlcv_uses_cache_when_available(mock_ohlcv_df):
    """Subsequent calls within 1 day use the cached file."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = mock_ohlcv_df

        trading_data.fetch_ohlcv("NVDA", "2023-01-01", "2023-01-10")
        trading_data.fetch_ohlcv("NVDA", "2023-01-01", "2023-01-10")

        # history() should only be called once; second call hit the cache
        assert MockTicker.return_value.history.call_count == 1


def test_fetch_ohlcv_invalidates_stale_cache(mock_ohlcv_df):
    """Cache older than 1 day is refreshed from the network."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = mock_ohlcv_df

        # Pre-create cache file
        cache_file = trading_data._cache_path("AMD", "2023-01-01", "2023-01-10")
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        mock_ohlcv_df.to_csv(cache_file)

        # Taint the file — make it 2 days old
        old_ts = time.time() - (2 * 86400)
        os.utime(cache_file, (old_ts, old_ts))

        trading_data.fetch_ohlcv("AMD", "2023-01-01", "2023-01-10")

        # Should have re-fetched
        assert MockTicker.return_value.history.call_count == 1


def test_fetch_ohlcv_handles_corrupt_cache(mock_ohlcv_df):
    """Corrupt cache file falls through to a network fetch."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = mock_ohlcv_df

        # Write invalid CSV
        cache_file = trading_data._cache_path("META", "2023-01-01", "2023-01-10")
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write("not,a,valid,data")

        # Should succeed by falling back to network
        result = trading_data.fetch_ohlcv("META", "2023-01-01", "2023-01-10")
        assert isinstance(result, pd.DataFrame)


# ── Error-handling tests ──────────────────────────────────────────


def test_fetch_ohlcv_handles_unknown_ticker():
    """Unknown ticker raises a clear YFException."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.side_effect = YFException("Invalid symbol")

        with pytest.raises(YFException, match="Failed to fetch"):
            trading_data.fetch_ohlcv("NOPE123", "2023-01-01", "2023-01-10")


def test_fetch_ohlcv_handles_network_failure():
    """Network errors surface with a descriptive message."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.side_effect = ConnectionError("Timeout")

        with pytest.raises(Exception, match="Failed to fetch"):
            trading_data.fetch_ohlcv("AAPL", "2023-01-01", "2023-01-10")


def test_fetch_ohlcv_rejects_bad_dates():
    """Non-parseable dates raise ValueError."""
    with pytest.raises(ValueError, match="Invalid date format"):
        trading_data.fetch_ohlcv("AAPL", "not-a-date", "2023-01-10")


def test_fetch_ohlcv_reports_missing_columns():
    """Response lacking OHLCV columns raises ValueError."""
    bad_df = pd.DataFrame({"foo": [1]}, index=pd.date_range("2023-01-01", periods=1))
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = bad_df

        with pytest.raises(ValueError, match="Missing column"):
            trading_data.fetch_ohlcv("AAPL", "2023-01-01", "2023-01-10")


# ── Cache file tests ──────────────────────────────────────────────


def test_fetch_ohlcv_writes_cache_file(mock_ohlcv_df):
    """Successful fetch stores data in the cache directory."""
    with patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = mock_ohlcv_df

        trading_data.fetch_ohlcv("GOOGL", "2023-01-01", "2023-01-10")

        cache_file = trading_data._cache_path("GOOGL", "2023-01-01", "2023-01-10")
        assert os.path.exists(cache_file)


def test_cache_path_is_deterministic():
    """Same inputs always yield the same cache path."""
    path1 = trading_data._cache_path("BTC-USD", "2022-01-01", "2022-12-31")
    path2 = trading_data._cache_path("BTC-USD", "2022-01-01", "2022-12-31")
    assert path1 == path2
