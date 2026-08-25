"""Tests for cerebral/trading/ticker_view.py (S29/#892, decisions #48-#51)."""
from types import SimpleNamespace

import pandas as pd
import pytest

from cerebral.trading.ticker_view import build_ticker_benchmark, build_ticker_view


def _fill(ts, phase="paper", side="buy", qty=1.0, price=100.0, pnl=0.0):
    return {"timestamp": ts, "phase": phase, "side": side, "qty": qty, "price": price, "pnl": pnl}


def _spec(symbol):
    return SimpleNamespace(symbol=symbol)


def _bars(closes, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def _fake_fetch_ohlcv(bars_by_symbol):
    def fetch(symbol, start, end, interval="1d"):
        return bars_by_symbol.get(symbol)
    return fetch


class TestBuildTickerBenchmark:
    def test_empty_fills_returns_empty(self):
        assert build_ticker_benchmark("AAPL", [], _fake_fetch_ohlcv({})) == []

    def test_scales_to_first_fills_real_qty_and_price(self):
        fills = [_fill("2026-01-01T00:00:00", qty=2.0, price=100.0)]
        fetch = _fake_fetch_ohlcv({"AAPL": _bars([100.0, 110.0, 90.0])})
        bench = build_ticker_benchmark("AAPL", fills, fetch)
        assert [round(b["value"], 2) for b in bench] == [0.0, 20.0, -20.0]

    def test_fetch_failure_returns_empty_not_raise(self):
        def broken_fetch(*a, **k):
            raise RuntimeError("network down")
        fills = [_fill("2026-01-01T00:00:00")]
        assert build_ticker_benchmark("AAPL", fills, broken_fetch) == []

    def test_no_bars_returns_empty(self):
        fills = [_fill("2026-01-01T00:00:00")]
        assert build_ticker_benchmark("AAPL", fills, _fake_fetch_ohlcv({"AAPL": None})) == []


class TestBuildTickerView:
    def test_watchlist_only_ticker_is_screened(self):
        result = build_ticker_view(
            watchlist_symbols=["NVDA"], states={}, get_spec=lambda sid: None,
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert result["tickers"] == [{"symbol": "NVDA", "stage": "screened", "strategies": []}]

    def test_strategy_with_no_fills_is_validated_not_screened(self):
        states = {"s1": SimpleNamespace(status="paper")}
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert len(result["tickers"]) == 1
        t = result["tickers"][0]
        assert t["symbol"] == "AAPL"
        assert t["stage"] == "validated"
        assert t["strategies"] == [{"name": "s1", "status": "paper", "segments": []}]

    def test_strategy_with_fills_is_charting_with_cumulative_equity(self):
        fills_desc = [  # get_fills returns most-recent-first, matching ForwardRecord
            _fill("2026-01-02T00:00:00", pnl=5.0, price=105.0),
            _fill("2026-01-01T00:00:00", pnl=0.0, price=100.0),
        ]
        states = {"s1": SimpleNamespace(status="paper")}
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: fills_desc,
            fetch_ohlcv=_fake_fetch_ohlcv({"AAPL": _bars([100.0, 105.0])}),
        )
        t = result["tickers"][0]
        assert t["stage"] == "charting"
        seg = t["strategies"][0]["segments"][0]
        assert seg["phase"] == "paper"
        # chronological order, cumulative pnl
        assert [p["equity"] for p in seg["points"]] == [0.0, 5.0]
        assert [p["ts"] for p in seg["points"]] == ["2026-01-01T00:00:00", "2026-01-02T00:00:00"]
        assert seg["points"][0]["strategy"] == "s1"

    def test_paper_and_live_render_as_separate_never_joined_segments(self):
        fills = [
            _fill("2026-01-03T00:00:00", phase="live", pnl=2.0, price=110.0, qty=1.0),
            _fill("2026-01-02T00:00:00", phase="paper", pnl=5.0, price=105.0),
            _fill("2026-01-01T00:00:00", phase="paper", pnl=0.0, price=100.0),
        ]
        states = {"s1": SimpleNamespace(status="live")}
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: fills, fetch_ohlcv=_fake_fetch_ohlcv({"AAPL": _bars([100.0])}),
        )
        segs = result["tickers"][0]["strategies"][0]["segments"]
        assert [s["phase"] for s in segs] == ["paper", "live"]
        # paper segment's cumulative equity never carries into live's
        assert [p["equity"] for p in segs[0]["points"]] == [0.0, 5.0]
        assert [p["equity"] for p in segs[1]["points"]] == [2.0]

    def test_multiple_strategies_on_same_ticker_both_listed(self):
        states = {
            "s1": SimpleNamespace(status="paper"),
            "s2": SimpleNamespace(status="paper"),
        }
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert len(result["tickers"]) == 1
        names = {s["name"] for s in result["tickers"][0]["strategies"]}
        assert names == {"s1", "s2"}

    def test_halted_with_no_fills_drops_off(self):
        states = {"s1": SimpleNamespace(status="halted")}
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert result["tickers"] == []

    def test_halted_with_fill_history_still_shows_charting(self):
        fills = [_fill("2026-01-01T00:00:00", pnl=3.0)]
        states = {"s1": SimpleNamespace(status="halted")}
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: fills, fetch_ohlcv=_fake_fetch_ohlcv({"AAPL": _bars([100.0])}),
        )
        assert len(result["tickers"]) == 1
        assert result["tickers"][0]["stage"] == "charting"

    def test_versioned_dispatch_id_resolves_spec_by_base_strategy_id(self):
        states = {"trend@v2": SimpleNamespace(status="paper")}
        seen_ids = []

        def get_spec(sid):
            seen_ids.append(sid)
            return _spec("MSFT")

        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=get_spec,
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert seen_ids == ["trend"]
        assert result["tickers"][0]["symbol"] == "MSFT"

    def test_unknown_spec_is_skipped_not_crash(self):
        states = {"ghost": SimpleNamespace(status="paper")}
        result = build_ticker_view(
            watchlist_symbols=[], states=states, get_spec=lambda sid: None,
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert result["tickers"] == []

    def test_watchlist_symbol_upgraded_to_charting_when_strategy_exists(self):
        fills = [_fill("2026-01-01T00:00:00", pnl=1.0)]
        states = {"s1": SimpleNamespace(status="paper")}
        result = build_ticker_view(
            watchlist_symbols=["AAPL"], states=states, get_spec=lambda sid: _spec("AAPL"),
            get_fills=lambda sid: fills, fetch_ohlcv=_fake_fetch_ohlcv({"AAPL": _bars([100.0])}),
        )
        assert len(result["tickers"]) == 1
        assert result["tickers"][0]["stage"] == "charting"

    def test_tickers_sorted_by_symbol(self):
        result = build_ticker_view(
            watchlist_symbols=["TSLA", "AAPL", "MSFT"], states={}, get_spec=lambda sid: None,
            get_fills=lambda sid: [], fetch_ohlcv=_fake_fetch_ohlcv({}),
        )
        assert [t["symbol"] for t in result["tickers"]] == ["AAPL", "MSFT", "TSLA"]
