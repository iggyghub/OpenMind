"""AlpacaMarketDataClient (S22/#875): get_bars must request the exact bar
resolution asked for, not a fixed 1-unit default. Never a real network call.
"""
import pandas as pd

from cerebral.trading.broker import AlpacaMarketDataClient


class _FakeBars:
    def __init__(self, df):
        self.df = df


class _FakeAlpacaClient:
    def __init__(self):
        self.last_request = None

    def get_stock_bars(self, req):
        self.last_request = req
        idx = pd.MultiIndex.from_tuples(
            [("AAPL", pd.Timestamp("2026-01-01"))], names=["symbol", "timestamp"]
        )
        df = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [100]},
            index=idx,
        )
        return _FakeBars(df)


class _SpyRequest:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _client_with_fakes():
    client = AlpacaMarketDataClient(env="paper")
    client._client = _FakeAlpacaClient()
    client._request_cls = _SpyRequest
    client._connected = True  # skip _connect() -- no credentials/network needed
    return client


def test_get_bars_requests_the_exact_resolution_not_a_1_unit_default():
    """TimeFrame.Minute/.Hour are fixed 1-unit constants -- using them
    directly for "5m"/"30m"/"4h" would silently request 1-minute/1-hour
    bars regardless of the interval actually asked for."""
    cases = {
        "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
        "1h": "1Hour", "4h": "4Hour", "1d": "1Day", "1w": "1Week", "1M": "1Month",
    }
    for interval, expected in cases.items():
        client = _client_with_fakes()
        client.get_bars("AAPL", "2026-01-01", "2026-01-10", interval)
        timeframe = client._client.last_request.kwargs["timeframe"]
        assert timeframe.value == expected, f"interval {interval!r} got {timeframe.value!r}"


def test_get_bars_unknown_interval_falls_back_to_daily():
    client = _client_with_fakes()
    client.get_bars("AAPL", "2026-01-01", "2026-01-10", "bogus")
    assert client._client.last_request.kwargs["timeframe"].value == "1Day"


def test_get_bars_returns_capitalised_ohlcv_columns():
    client = _client_with_fakes()
    df = client.get_bars("AAPL", "2026-01-01", "2026-01-10", "1d")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_get_bars_collapses_the_multiindex_to_a_plain_date_index():
    """Real bug, live-observed: get_stock_bars always returns a MultiIndex
    (symbol, timestamp), even for one symbol. df.index.name = "Date"
    silently no-ops on a MultiIndex (nothing raises, but the index keeps
    its (symbol, timestamp) shape) -- fetch_ohlcv's documented contract
    (a single DatetimeIndex named "Date", matching yfinance's shape) was
    never actually met once Alpaca credentials existed to take this path
    for real."""
    client = _client_with_fakes()
    df = client.get_bars("AAPL", "2026-01-01", "2026-01-10", "1d")
    assert df.index.nlevels == 1
    assert df.index.name == "Date"
    assert df.index[0] == pd.Timestamp("2026-01-01")
