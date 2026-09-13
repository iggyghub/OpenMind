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
    """Redirect both cache mechanisms to a per-test tmp dir.

    Without this, tests sharing a symbol/date-range key (several use AAPL /
    2023-01-01 / 2023-01-10) silently read each other's cached CSV instead of
    exercising their own mocked scenario. Since RP3, fetch_ohlcv's Alpaca
    path routes through cerebral/trading/bar_cache.py's SQLite store instead
    of a CSV file -- its `data_dir()` call must be redirected too (patching
    `cerebral.paths.data_dir` itself, since bar_cache imports it lazily
    per-call), or these tests would create/use the real
    cerebral/data/bars.db on whatever machine runs them.
    """
    monkeypatch.setattr(trading_data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("cerebral.paths.data_dir", lambda: tmp_path)


@pytest.fixture(autouse=True)
def no_alpaca_market_data(monkeypatch):
    """fetch_ohlcv tries AlpacaMarketDataClient first, falling back to
    yfinance only on exception -- these tests are specifically exercising
    the yfinance path (mocking yf.Ticker), so once real Alpaca paper
    credentials exist in keyring, every test here silently started making
    a real network call to Alpaca instead of ever touching the mock. Force
    the Alpaca branch to fail so it falls through, restoring the "no real
    network requests" contract in this file's own module docstring.

    Patched in BOTH places (RP3): cerebral.trading.bar_cache imports
    AlpacaMarketDataClient at module level (a persistent binding, needed so
    cerebral/tests/test_bar_cache.py can itself patch
    cerebral.trading.bar_cache.AlpacaMarketDataClient) -- patching only
    cerebral.trading.broker's own attribute would not reach that
    already-bound reference, since fetch_ohlcv's cache=True path now goes
    through bar_cache, not a direct broker.AlpacaMarketDataClient() call.
    """
    class _AlwaysFails:
        def __init__(self, *a, **kw):
            raise RuntimeError("Alpaca disabled in trading_data tests")
    monkeypatch.setattr("cerebral.trading.broker.AlpacaMarketDataClient", _AlwaysFails)
    monkeypatch.setattr("cerebral.trading.bar_cache.AlpacaMarketDataClient", _AlwaysFails)


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


def test_fetch_ohlcv_routes_through_bar_cache_when_alpaca_available():
    """RP3: cache=True routes the Alpaca path through
    cerebral.trading.bar_cache.get_bars, not this module's own CSV cache
    or yfinance -- the original RP3 PR added bar_cache.py but never wired
    fetch_ohlcv to actually use it, so nothing exercised this integration."""
    cached_df = pd.DataFrame({
        "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0],
    }, index=pd.to_datetime(["2023-01-01"]))
    cached_df.index.name = "Date"

    with patch("cerebral.trading.bar_cache.get_bars", return_value=cached_df) as mock_get_bars, \
         patch("cerebral.trading_data.yf.Ticker") as MockTicker:
        result = trading_data.fetch_ohlcv("AAPL", "2023-01-01", "2023-01-01")

        mock_get_bars.assert_called_once_with("AAPL", "2023-01-01", "2023-01-01", "1d")
        MockTicker.assert_not_called()
        pd.testing.assert_frame_equal(result, cached_df)


def test_fetch_ohlcv_cache_false_bypasses_bar_cache():
    """cache=False keeps the pre-RP3 behavior: a direct AlpacaMarketDataClient
    call, no SQLite cache involved at all."""
    direct_df = pd.DataFrame({
        "Open": [2.0], "High": [2.0], "Low": [2.0], "Close": [2.0], "Volume": [2.0],
    }, index=pd.to_datetime(["2023-01-01"]))

    mock_client = MagicMock()
    mock_client.get_bars.return_value = direct_df
    with patch("cerebral.trading.broker.AlpacaMarketDataClient", return_value=mock_client), \
         patch("cerebral.trading.bar_cache.get_bars") as mock_cached_get_bars:
        result = trading_data.fetch_ohlcv("AAPL", "2023-01-01", "2023-01-01", cache=False)

        mock_cached_get_bars.assert_not_called()
        pd.testing.assert_frame_equal(result, direct_df)


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
