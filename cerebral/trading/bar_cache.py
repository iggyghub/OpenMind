import sqlite3
import os
from datetime import datetime
import pandas as pd


def get_bars(symbol: str, start: str, end: str, interval: str = "1d", refresh: bool = False) -> pd.DataFrame:
    # ponytail: split_after_cutoff
    from cerebral.paths import data_dir
    from cerebral.trading.broker import AlpacaMarketDataClient

    db_path = os.path.join(data_dir(), "bars.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
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
        """)
        conn.commit()

        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = ? AND interval = ? AND ts >= ? AND ts <= ?",
            conn,
            params=(symbol, interval, start, end),
            parse_dates=["ts"]
        )

        needs_refresh = refresh
        max_cached_ts = None
        if not df.empty:
            df.index.name = "Date"
            df = df[["open", "high", "low", "close", "volume"]]
            df.columns = ["Open", "High", "Low", "Close", "Volume"]
            max_cached_ts = df["Date"].max()
            if not needs_refresh:
                if pd.isnull(max_cached_ts) or pd.to_datetime(end) > max_cached_ts:
                    needs_refresh = True

        if not needs_refresh:
            return df

        fetch_start = start
        if max_cached_ts is not None and not pd.isnull(max_cached_ts):
            fetch_start = max_cached_ts.strftime("%Y-%m-%d")

        client = AlpacaMarketDataClient("paper")
        new_df = client.get_bars(symbol, fetch_start, end, interval)

        if not new_df.empty:
            records = []
            for ts, row in new_df.iterrows():
                fetched_at = datetime.now().isoformat()
                records.append((
                    symbol, interval, ts.strftime("%Y-%m-%d"),
                    row["Open"], row["High"], row["Low"], row["Close"], row["Volume"],
                    fetched_at
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO bars (symbol, interval, ts, open, high, low, close, volume, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                records
            )
            conn.commit()

        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = ? AND interval = ? AND ts >= ? AND ts <= ?",
            conn,
            params=(symbol, interval, start, end),
            parse_dates=["ts"]
        )
        df.index.name = "Date"
        df = df[["open", "high", "low", "close", "volume"]]
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        return df
    finally:
        conn.close()
