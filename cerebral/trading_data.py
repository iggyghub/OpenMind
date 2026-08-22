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
from datetime import datetime

import pandas as pd
import yfinance as yf


def _cache_path(symbol: str, start: str, end: str) -> str:
    """Generate a deterministic cache file path for a given ticker and date range."""
    cache_dir = os.path.join(os.path.dirname(__file__), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    # Sanitize symbol for filesystem safety
    safe_symbol = "".join(c if c.isalnum() else "-" for c in symbol)
    filename = f"{safe_symbol}_{start}_{end}.csv"
    return os.path.join(cache_dir, filename)


def fetch_ohlcv(
    symbol: str,
    start: str,
    end: str,
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
    yf.YFError
        If yfinance fails to fetch data
    """
    # Validate inputs
    try:
        pd.to_datetime(start)
        pd.to_datetime(end)
    except ValueError as e:
        raise ValueError(f"Invalid date format: {e}") from e

    # Check cache
    if cache:
        cache_file = _cache_path(symbol, start, end)
        if os.path.exists(cache_file):
            # Re-fetch if cached file is older than 1 day
            file_age = datetime.now() - datetime.fromtimestamp(
                os.path.getmtime(cache_file)
            )
            if file_age.days < 1:
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
        df = ticker.history(start=start, end=end)
    except Exception as e:
        raise yf.YFError(f"Failed to fetch data for {symbol}: {e}") from e

    # yfinance returns Dividends and Stock Splits columns too — keep only OHLCV
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(
                f"Missing column {col} in yfinance response for {symbol}"
            )
    df = df[required_cols]

    # Drop rows where all values are NaN
    df.dropna(how="all", inplace=True)

    # Cache result
    if cache:
        try:
            cache_file = _cache_path(symbol, start, end)
            df.to_csv(cache_file)
        except Exception:
            pass  # Non-fatal — caller still gets data

    return df
