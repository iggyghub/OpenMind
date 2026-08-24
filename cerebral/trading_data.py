"""
Module for fetching and caching daily OHLCV bars via yfinance.

Returns a pandas DataFrame with columns: Open, High, Low, Close, Volume,
adjusted for splits/dividends. Supports any ticker symbol (equities, ETFs,
crypto via yfinance's coverage). Caching avoids redundant network calls for
the same ticker/date range.

Survivorship bias caveat: yfinance does not include delisted tickers.
Historical data for delisted companies may be incomplete or unavailable,
which can skew backtesting results if not accounted for.
"""

import os
import time
from datetime import datetime

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFException

# Module-level so tests can monkeypatch it to a tmp dir for isolation.
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


def _cache_path(symbol: str, start: str, end: str, interval: str = "1d") -> str:
    """Generate a deterministic cache file path for a given ticker and date range."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    # Sanitize symbol for filesystem safety
    safe_symbol = "".join(c if c.isalnum() else "-" for c in symbol)
    filename = f"{safe_symbol}_{start}_{end}_{interval}.csv"
    return os.path.join(_CACHE_DIR, filename)


def fetch_ohlcv(
    symbol: str,
    start: str,
    end: str,
    interval: str = "1d",
    cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch OHLCV data for a ticker symbol with optional caching.

    Parameters
    ----------
    symbol : str
        Ticker symbol (equities, ETFs, crypto via yfinance)
    start : str
        Start date in YYYY-MM-DD format
    end : str
        End date in YYYY-MM-DD format
    interval : str
        Bar resolution (e.g., "1d", "5m", "1h"). Passed through to data source.
    cache : bool
        If True, use local file cache to avoid redundant network calls

    Returns
    -------
    pd.DataFrame
        DataFrame with DatetimeIndex and columns: Open, High, Low, Close, Volume
        Data is adjusted for splits and dividends.

    Raises
    ------
    ValueError
        If symbol is invalid or date range is invalid
    yfinance.exceptions.YFException
        If yfinance fails to fetch data
    """
    # Validate inputs
    try:
        pd.to_datetime(start)
        pd.to_datetime(end)
    except ValueError as e:
        raise ValueError(f"Invalid date format: {e}") from e

    # Try Alpaca Market Data first (preferred per decision #39)
    try:
        from cerebral.trading.broker import AlpacaMarketDataClient
        client = AlpacaMarketDataClient("paper")
        df = client.get_bars(symbol, start, end, interval)
        if cache:
            cache_file = _cache_path(symbol, start, end, interval)
            try:
                df.to_csv(cache_file)
            except Exception:
                pass
        return df
    except Exception:
        pass  # Falls through to yfinance

    # Cache TTL: daily is fine for a day, intraday stalifies in ~1 hour.
    ttl_seconds = 86400 if interval == "1d" else 3600
    if cache:
        cache_file = _cache_path(symbol, start, end, interval)
        if os.path.exists(cache_file):
            if time.time() - os.path.getmtime(cache_file) < ttl_seconds:
                try:
                    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                    # Ensure columns are correctly named
                    df.columns = ["Open", "High", "Low", "Close", "Volume"]
                    return df
                except Exception:
                    # Corrupt or unreadable cache — fall through to network fetch
                    pass

    # Fetch from yfinance
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval=interval)
    except Exception as e:
        raise YFException(f"Failed to fetch data for {symbol}: {e}") from e

    # yfinance returns Dividends and Stock Splits columns too — keep only OHLCV
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(
                f"Missing column {col} in yfinance response for {symbol}"
            )

    # Drop rows where all values are NaN. Index name set here (not just on the
    # cache-write side) survives the cache round-trip: pandas writes the index
    # name as the CSV's first column header, so a cached read picks it back up.
    df = df[required_cols].dropna(how="all")
    df.index.name = "Date"

    # Cache result
    if cache:
        try:
            cache_file = _cache_path(symbol, start, end, interval)
            df.to_csv(cache_file)
        except Exception:
            pass  # Non-fatal — caller still gets data

    return df
