"""Full tests for trading_replay tools against tmp-path-backed real stores
(not mocks standing in for sqlite3.Row/StrategySpec's actual shapes --
ReplayStore rows only support ["column"] access, not .get()/.attribute, and
StrategySpec's real field is strategy_id, not id).
"""
import asyncio
import json

import pytest

from cerebral.trading.strategy_store import StrategyStore, StrategySpec
from cerebral.trading.replay_store import ReplayStore
from plugins import trading_replay as tr


def _content(result):
    assert not result.is_error, result.content
    return json.loads(result.content)


@pytest.fixture(autouse=True)
def _reset_cache_warm_state():
    """start_cache_warm/stop_cache_warm track their running task via
    module-level globals (matches this module's existing design, not
    per-plugin-instance state) -- reset around every test in this file so
    one test's task/flag never leaks into the next."""
    tr._cache_warm_task = None
    tr._cache_warm_stop_flag = False
    yield
    tr._cache_warm_task = None
    tr._cache_warm_stop_flag = False


def test_list_strategies_returns_filtered_json(tmp_path, monkeypatch):
    store = StrategyStore(db_path=tmp_path / "strategy.db")
    store.save(StrategySpec("s1", "AAPL", "def strategy(d): return [1]*len(d)", interval="1h"))
    store.save(StrategySpec("s2", "AAPL", "def strategy(d): return [1]*len(d)", interval="4h"))
    store.save(StrategySpec("s3", "MSFT", "def strategy(d): return [1]*len(d)", interval="1h"))
    monkeypatch.setattr(tr, "StrategyStore", lambda: store)

    plugin = tr.create()

    data = _content(asyncio.run(plugin.call_tool("list_strategies", {})))
    assert len(data) == 3
    assert all(k in data[0] for k in ("id", "symbol", "interval", "qty"))

    data = _content(asyncio.run(plugin.call_tool("list_strategies", {"symbol": "AAPL"})))
    assert len(data) == 2 and all(d["symbol"] == "AAPL" for d in data)

    data = _content(asyncio.run(plugin.call_tool("list_strategies", {"interval": "1h"})))
    assert len(data) == 2 and all(d["interval"] == "1h" for d in data)

    data = _content(asyncio.run(plugin.call_tool("list_strategies", {"symbol": "AAPL", "interval": "1h"})))
    assert len(data) == 1 and data[0]["id"] == "s1"


def test_simulate_period_returns_run_id_and_summary(tmp_path, monkeypatch):
    store = StrategyStore(db_path=tmp_path / "strategy.db")
    store.save(StrategySpec("s1", "AAPL", "def strategy(d): return [1]*len(d)", interval="1h"))
    store.save(StrategySpec("s2", "MSFT", "def strategy(d): return [1]*len(d)", interval="4h"))
    monkeypatch.setattr(tr, "StrategyStore", lambda: store)

    replay_store = ReplayStore(db_path=str(tmp_path / "replay.db"))
    monkeypatch.setattr(tr, "ReplayStore", lambda: replay_store)

    def fake_run_replay(specs, start, end):
        run_id = replay_store.create_run(start, end, "1h", len(specs))
        # Only one spec should reach here (symbols=["AAPL"] filter applied
        # by the plugin BEFORE calling run_replay).
        assert [s.strategy_id for s in specs] == ["s1"]
        replay_store.record_result(
            run_id, "s1", "AAPL", gross_return=0.06, net_return=0.05,
            n_trades=2, max_drawdown=-0.01, sharpe=1.2, flat_reason=None,
        )
        return run_id
    monkeypatch.setattr(tr, "run_replay", fake_run_replay)

    plugin = tr.create()
    data = _content(asyncio.run(plugin.call_tool(
        "simulate_period", {"start": "2024-01-01", "end": "2024-01-02", "symbols": ["AAPL"]}
    )))

    assert data["total_strategies_replayed"] == 1
    assert data["flat_reason_count"] == 0
    assert data["aggregate_mean_net_return"] == 0.05


def test_simulate_period_requires_start_and_end():
    plugin = tr.create()
    result = asyncio.run(plugin.call_tool("simulate_period", {"start": "2024-01-01"}))
    assert result.is_error is True


def test_replay_report_returns_full_data_and_census(tmp_path, monkeypatch):
    replay_store = ReplayStore(db_path=str(tmp_path / "replay.db"))
    monkeypatch.setattr(tr, "ReplayStore", lambda: replay_store)

    run_id = replay_store.create_run("2024-01-01", "2024-01-02", "1h", 3)
    replay_store.record_result(run_id, "a", "A", 0.1, 0.1, 1, -0.01, 0.5,
                                "AttributeError: 'Series' object has no attribute 'x'")
    replay_store.record_result(run_id, "b", "B", 0.2, 0.2, 2, -0.02, 0.7,
                                "AttributeError: 'DataFrame' object has no attribute 'y'")
    replay_store.record_result(run_id, "c", "C", -0.1, -0.1, 0, -0.05, -0.3, None)

    plugin = tr.create()
    data = _content(asyncio.run(plugin.call_tool("replay_report", {"run_id": run_id})))

    assert data["run_id"] == run_id
    assert data["total_strategies"] == 3
    assert len(data["rows"]) == 3
    assert data["flat_reason_census"] == {"AttributeError": 2}
    assert data["rows"][0]["flat_reason"] is not None
    assert data["rows"][2]["flat_reason"] is None


def test_replay_report_unknown_run_returns_error(tmp_path, monkeypatch):
    replay_store = ReplayStore(db_path=str(tmp_path / "replay.db"))
    monkeypatch.setattr(tr, "ReplayStore", lambda: replay_store)

    plugin = tr.create()
    result = asyncio.run(plugin.call_tool("replay_report", {"run_id": "does-not-exist"}))
    assert result.is_error is True
    assert "does-not-exist" in result.content


def test_mcporchestrator_discovers_trading_replay_without_refusal():
    """Smoke-test plugin discovery with verify_test_files=True, per
    CLAUDE.md: a bare MCPOrchestrator() defaults that to False and would
    NOT catch a missing/misnamed gate file."""
    from pathlib import Path
    from cerebral.main import MCPOrchestrator

    plugins_dir = Path(__file__).resolve().parents[2] / "plugins"
    orc = MCPOrchestrator(verify_test_files=True)
    orc.discover_plugins(plugins_dir)

    assert "trading_replay" in orc._plugins
    refusals = [e for e in orc.registration_errors if e.get("plugin_name") == "trading_replay"]
    assert refusals == []


# ── RP7: start_cache_warm / stop_cache_warm ─────────────────────────


def test_compute_universe_uses_explicit_symbols_when_given():
    assert tr._compute_universe(["X", "Y"]) == ["X", "Y"]


def test_compute_universe_unions_strategies_and_watchlist_without_duplicates(monkeypatch):
    class FakeStore:
        def list_all(self):
            return [StrategySpec("s1", "AAPL", "code"), StrategySpec("s2", "TSLA", "code")]

    class FakeWatchlist:
        def symbols(self):
            return ["TSLA", "NVDA"]  # TSLA overlaps -- must not duplicate

    monkeypatch.setattr(tr, "StrategyStore", FakeStore)
    monkeypatch.setattr(tr, "DiscoveryWatchlist", FakeWatchlist)

    universe = tr._compute_universe(None)
    assert sorted(universe) == ["AAPL", "NVDA", "TSLA"]
    assert len(universe) == len(set(universe))


def test_start_cache_warm_retries_once_then_succeeds(monkeypatch):
    calls = []

    class FlakyBarCache:
        def get_bars(self, symbol, start, end, interval):
            calls.append(symbol)
            if symbol == "FLAKY" and calls.count("FLAKY") == 1:
                raise RuntimeError("transient failure")
            return "ok"

    monkeypatch.setattr(tr, "bar_cache", FlakyBarCache())

    async def scenario():
        msg = await tr.start_cache_warm(interval="1d", symbols=["FLAKY", "OK"])
        assert msg == "Cache warm started."
        await tr._cache_warm_task  # wait for the background task to finish

    asyncio.run(scenario())

    assert calls.count("FLAKY") == 2  # failed once, retried, succeeded
    assert "OK" in calls


def test_start_cache_warm_gives_up_after_three_failures(monkeypatch):
    calls = []

    class AlwaysFailsBarCache:
        def get_bars(self, symbol, start, end, interval):
            calls.append(symbol)
            raise RuntimeError("permanent failure")

    monkeypatch.setattr(tr, "bar_cache", AlwaysFailsBarCache())

    async def scenario():
        await tr.start_cache_warm(interval="1d", symbols=["BROKEN"])
        await tr._cache_warm_task

    asyncio.run(scenario())  # must not raise -- a symbol's failure is swallowed, not fatal

    assert calls.count("BROKEN") == 3  # exactly 3 attempts, no more


def test_stop_cache_warm_halts_before_all_symbols_are_fetched(monkeypatch):
    calls = []

    class RecordingBarCache:
        def get_bars(self, symbol, start, end, interval):
            calls.append(symbol)
            return "ok"

    monkeypatch.setattr(tr, "bar_cache", RecordingBarCache())

    async def scenario():
        await tr.start_cache_warm(interval="1d", symbols=["A", "B", "C"])
        await asyncio.sleep(0)  # let the task begin before we stop it
        msg = await tr.stop_cache_warm()
        assert msg == "Cache warm stopped."

    asyncio.run(scenario())

    assert len(calls) < 3, "stop_cache_warm should prevent every symbol from being fetched"


def test_stop_cache_warm_with_nothing_running():
    msg = asyncio.run(tr.stop_cache_warm())
    assert msg == "No cache warm running."


def test_start_cache_warm_refuses_a_second_concurrent_run(monkeypatch):
    class SlowBarCache:
        def get_bars(self, symbol, start, end, interval):
            return "ok"

    monkeypatch.setattr(tr, "bar_cache", SlowBarCache())

    async def scenario():
        first = await tr.start_cache_warm(interval="1d", symbols=["A"])
        second = await tr.start_cache_warm(interval="1d", symbols=["B"])
        assert first == "Cache warm started."
        assert second == "Cache warm already running."
        await tr._cache_warm_task

    asyncio.run(scenario())


def test_plugin_call_tool_dispatches_cache_warm_tools(monkeypatch):
    monkeypatch.setattr(tr, "_compute_universe", lambda symbols: [])
    plugin = tr.create()

    result = asyncio.run(plugin.call_tool("start_cache_warm", {}))
    assert result.content == "Cache warm started."

    result = asyncio.run(plugin.call_tool("stop_cache_warm", {}))
    assert result.content in ("Cache warm stopped.", "No cache warm running.")
