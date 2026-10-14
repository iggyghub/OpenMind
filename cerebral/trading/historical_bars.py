"""Long-history daily bars for stress-window research (yfinance, split/dividend adjusted).

Why this exists: the normal bar cache (cerebral.trading.bar_cache) is fed by Alpaca market data,
which does not reach back before ~2016 on the free tier, so the 2008 crash and the 2010s are
unreachable through it. This is a deliberately separate, research-only loader with its own cache
table so it can never contaminate or confuse the live bar cache.

Survivorship caveat: the symbols researched are today's large caps, so every window here is
biased toward names that survived. That flatters buy-and-hold and every strategy alike.

Informational research plumbing only -- nothing here feeds live decisions.
"""
import logging
import sqlite3
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

DB_PATH = data_dir() / "bars_hist.db"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _download(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=True, progress=False)
    return df


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance returns MultiIndex columns ('Close', 'AAPL'); flatten to plain OHLCV, tz-naive, sorted."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df[[c for c in COLUMNS if c in df.columns]].dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.sort_index()


def get_daily_bars(
    symbol: str, start: str, end: str,
    *, db_path: Optional[Path] = None, download: Optional[Callable] = None,
) -> pd.DataFrame:
    """Adjusted daily bars for [start, end). Served from the local cache when it already covers the
    range; otherwise fetched once and stored. An empty frame means the symbol has no data there."""
    db_path = Path(db_path or DB_PATH)
    download = download or _download
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bars_hist (symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, "
            "close REAL, volume REAL, PRIMARY KEY (symbol, ts))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bars_hist_cover (symbol TEXT, start TEXT, end TEXT, "
            "PRIMARY KEY (symbol, start, end))"
        )
        covered = conn.execute(
            "SELECT 1 FROM bars_hist_cover WHERE symbol = ? AND start <= ? AND end >= ?",
            (symbol, start, end),
        ).fetchone()
        if not covered:
            fresh = _normalise(download(symbol, start, end))
            if not fresh.empty:
                conn.executemany(
                    "INSERT OR REPLACE INTO bars_hist VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (symbol, ts.strftime("%Y-%m-%d"), float(r.get("Open", r["Close"])),
                         float(r.get("High", r["Close"])), float(r.get("Low", r["Close"])),
                         float(r["Close"]), float(r.get("Volume", 0.0)))
                        for ts, r in fresh.iterrows()
                    ],
                )
            # Record coverage even when empty so a symbol with no data is not re-fetched every run.
            conn.execute("INSERT OR REPLACE INTO bars_hist_cover VALUES (?, ?, ?)", (symbol, start, end))
            conn.commit()
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM bars_hist "
            "WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (symbol, start, end),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df.pop("ts"))
    return df
