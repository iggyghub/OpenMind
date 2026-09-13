"""Unit tests for cerebral/trading/replay.py (RP1)."""
import pandas as pd
import pytest

from cerebral.trading.replay import run_bars, derive_trades


_ALWAYS_LONG = "def strategy(data):\n    return [1] * len(data)\n"


def _ramp_bars(n: int = 30) -> pd.DataFrame:
    """Synthetic strictly-increasing daily OHLCV bars."""
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * n},
        index=idx,
    )


def test_run_bars_equity_nondecreasing():
    bars = _ramp_bars(30)
    equity, position, metrics = run_bars(_ALWAYS_LONG, bars, "1d")
    assert len(equity) == 30
    for i in range(1, len(equity)):
        assert equity[i] >= equity[i - 1] - 1e-9, f"equity dropped at bar {i}"


def test_run_bars_position_all_ones():
    bars = _ramp_bars(30)
    _, position, _ = run_bars(_ALWAYS_LONG, bars, "1d")
    # shift(1) means bar 0 is 0.0 (no prior signal), bars 1+ are 1.0
    assert position.iloc[0] == pytest.approx(0.0)
    assert (position.iloc[1:] == 1.0).all()


def test_run_bars_returns_position_series():
    bars = _ramp_bars(10)
    _, position, _ = run_bars(_ALWAYS_LONG, bars, "1d")
    assert isinstance(position, pd.Series)
    assert len(position) == 10


def test_run_bars_metrics_has_max_holding_days():
    bars = _ramp_bars(20)
    _, _, metrics = run_bars(_ALWAYS_LONG, bars, "1d")
    assert "max_holding_days" in metrics


def test_derive_trades_flat_long_flat():
    pos = pd.Series([0, 1, 1, 1, 0, 0], index=range(6))
    close = pd.Series([10.0, 10.0, 10.0, 10.0, 10.0, 10.0], index=range(6))
    trades = derive_trades(pos, close)
    assert len(trades) == 2
    assert trades[0].direction == "buy"
    assert trades[0].price == 10.0
    assert trades[0].value == 10.0
    assert trades[1].direction == "sell"
    assert trades[1].price == 10.0
    assert trades[1].value == 10.0


def test_derive_trades_no_changes():
    # Genuinely flat throughout, including bar 0 -- 0 vs the implicit prior 0
    # is not a change. (A CONSTANT nonzero position, e.g. 0.5 the whole way,
    # is a different case: bar 0 is then a real entry from flat and correctly
    # counts as one trade -- see the "first bar counts as a change from an
    # implicit 0" rule in derive_trades' docstring.)
    pos = pd.Series([0.0, 0.0, 0.0], index=range(3))
    close = pd.Series([10.0, 10.0, 10.0], index=range(3))
    trades = derive_trades(pos, close)
    assert len(trades) == 0


def test_derive_trades_constant_nonzero_position_counts_one_entry():
    # Never literally flat, but bar 0 is still a real entry from an implicit
    # prior 0 -- one buy, not zero.
    pos = pd.Series([0.5, 0.5, 0.5], index=range(3))
    close = pd.Series([10.0, 10.0, 10.0], index=range(3))
    trades = derive_trades(pos, close)
    assert len(trades) == 1
    assert trades[0].direction == "buy"
    assert trades[0].index == 0


def test_net_returns_leq_gross_returns():
    bars = _ramp_bars(30)
    # Strategy that swings long/flat to generate trades. n//2-1 + n-n//2 == n-1,
    # plus the two explicit [1] entries == n elements total -- must match len(data).
    _swing = """
def strategy(data):
    n = len(data)
    return [1] + [0] * (n // 2 - 1) + [1] + [0] * (n - n // 2 - 1)
"""
    equity, position, metrics = run_bars(_swing, bars, "1d")
    gross_returns = metrics["gross_returns"]
    net_returns = metrics["net_returns"]
    assert len(net_returns) == len(gross_returns)
    for g, n in zip(gross_returns, net_returns):
        assert n <= g + 1e-9, f"Net return {n} > gross return {g}"


def test_run_replay_portfolio(tmp_path):
    """run_replay against synthetic specs, a fake bar_cache, and a real
    ReplayStore. Uses the REAL StrategySpec (cerebral.trading.strategy_store)
    -- the same dataclass live dispatch and run_gauntlet use -- not a
    reinvented local one, since run_replay must accept exactly what
    StrategyStore.list_all() (RP6) will actually hand it."""
    from cerebral.trading.replay import run_replay
    from cerebral.trading.replay_store import ReplayStore
    from cerebral.trading.strategy_store import StrategySpec

    class FakeBarCache:
        """Matches cerebral.trading.bar_cache's real free-function shape --
        get_bars(symbol, start, end, interval), not a duck-typed .fetch()
        with a different argument order. Ignores start/end and returns 50
        synthetic bars regardless, wide enough to cover any interval's
        warm-up margin plus the requested window."""
        def get_bars(self, symbol: str, start: str, end: str, interval: str):
            idx = pd.date_range(start="2023-06-01", periods=250, freq="B")
            close = [100.0 + i * 0.1 for i in range(250)]
            return pd.DataFrame(
                {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * 250},
                index=idx,
            )

    bar_cache = FakeBarCache()
    replay_store = ReplayStore(db_path=str(tmp_path / "replay.db"))

    always_long = StrategySpec("s-always-long", "SPY", "def strategy(data):\n    return [1] * len(data)\n", interval="1d")
    broken = StrategySpec("s-broken", "TSLA", "def strategy(data):\n    raise RuntimeError('kaboom')\n", interval="1d")
    warmup_need = StrategySpec(
        "s-warmup", "AAPL",
        "def strategy(data):\n    n = len(data)\n    return [0] * 19 + [1] * (n - 19)\n",
        interval="1d",
    )

    specs = [always_long, broken, warmup_need]
    start = "2024-01-01"
    end = "2024-01-10"

    run_id = run_replay(specs, start, end, bar_cache=bar_cache, replay_store=replay_store)

    assert run_id is not None and isinstance(run_id, str)

    # ReplayStore rows are sqlite3.Row -- dict-style ["column"] access, not
    # attribute access.
    runs = replay_store.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["n_strategies"] == 3

    results = replay_store.get_results(run_id)
    results_map = {r["strategy_id"]: r for r in results}
    assert set(results_map) == {"s-always-long", "s-broken", "s-warmup"}

    always_res = results_map["s-always-long"]
    assert always_res["net_return"] is not None
    assert always_res["net_return"] != 0.0
    assert always_res["flat_reason"] is None

    broken_res = results_map["s-broken"]
    assert broken_res["flat_reason"] is not None
    assert "kaboom" in broken_res["flat_reason"]

    # Warm-up strategy should not fail -- correct all-flat-during-warm-up
    # behaviour is not a code failure.
    warmup_res = results_map["s-warmup"]
    assert warmup_res["flat_reason"] is None


def test_run_replay_broken_strategy_does_not_abort_the_run(tmp_path):
    """One strategy raising must not prevent the others in the same run
    from being recorded."""
    from cerebral.trading.replay import run_replay
    from cerebral.trading.replay_store import ReplayStore
    from cerebral.trading.strategy_store import StrategySpec

    class FakeBarCache:
        def get_bars(self, symbol, start, end, interval):
            idx = pd.date_range(start="2023-06-01", periods=250, freq="B")
            close = [100.0 + i * 0.1 for i in range(250)]
            return pd.DataFrame(
                {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * 250},
                index=idx,
            )

    replay_store = ReplayStore(db_path=str(tmp_path / "replay2.db"))
    specs = [
        StrategySpec("s-a", "AAA", "def strategy(data):\n    raise ValueError('bad')\n", interval="1d"),
        StrategySpec("s-b", "BBB", _ALWAYS_LONG, interval="1d"),
    ]
    run_id = run_replay(specs, "2024-01-01", "2024-01-10", bar_cache=FakeBarCache(), replay_store=replay_store)

    results = {r["strategy_id"]: r for r in replay_store.get_results(run_id)}
    assert results["s-a"]["flat_reason"] is not None
    assert results["s-b"]["flat_reason"] is None
    assert results["s-b"]["net_return"] is not None


def test_run_repopulates_news_event_count(tmp_path):
    """Confirm run_replay populates news_event_count when news data exists, and 0 when none."""
    from cerebral.trading.replay import run_replay
    from cerebral.trading.replay_store import ReplayStore
    from cerebral.trading.strategy_store import StrategySpec

    class FakeBarCache:
        def get_bars(self, symbol, start, end, interval):
            idx = pd.date_range(start="2023-06-01", periods=250, freq="B")
            close = [100.0 + i * 0.1 for i in range(250)]
            return pd.DataFrame(
                {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * 250},
                index=idx,
            )

    replay_store = ReplayStore(db_path=str(tmp_path / "replay_news.db"))

    spec = StrategySpec("s-news", "TSLA", _ALWAYS_LONG, interval="1d")
    
    # Patch news_cache to simulate existing news data
    with patch("cerebral.trading.replay.news_cache") as mock_news:
        mock_news.count_news_events.return_value = 2  # Simulate 2 events per day
        
        run_id = run_replay([spec], "2024-01-01", "2024-01-03", bar_cache=FakeBarCache(), replay_store=replay_store)

        results = {r["strategy_id"]: r for r in replay_store.get_results(run_id)}
        assert results["s-news"]["news_event_count"] == 6  # 3 days * 2 events

    # Test 0 when no news cached
    with patch("cerebral.trading.replay.news_cache") as mock_news:
        mock_news.count_news_events.return_value = 0
        
        spec2 = StrategySpec("s-no-news", "AAPL", _ALWAYS_LONG, interval="1d")
        run_id2 = run_replay([spec2], "2024-02-01", "2024-02-02", bar_cache=FakeBarCache(), replay_store=replay_store)
        
        results2 = {r["strategy_id"]: r for r in replay_store.get_results(run_id2)}
        assert results2["s-no-news"]["news_event_count"] == 0
