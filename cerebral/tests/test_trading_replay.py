"""Full tests for trading_replay tools against tmp-path-backed real stores
(not mocks standing in for sqlite3.Row/StrategySpec's actual shapes --
ReplayStore rows only support ["column"] access, not .get()/.attribute, and
StrategySpec's real field is strategy_id, not id).
"""
import asyncio
import json

from cerebral.trading.strategy_store import StrategyStore, StrategySpec
from cerebral.trading.replay_store import ReplayStore
from plugins import trading_replay as tr


def _content(result):
    assert not result.is_error, result.content
    return json.loads(result.content)


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
