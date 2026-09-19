"""CrossStockStore.get_pair_returns_by_strategy + the status payload's vs-benchmark view."""
import json

from cerebral.trading.cross_stock_store import CrossStockStore, MIN_TRADES_FLOOR


def _fill(store, sid, rows):
    run = store.create_run("2021-01-01", "2026-01-01")
    for i, (net, bench, trades) in enumerate(rows):
        store.record_result(run, sid, f"S{i}", net, -0.1, trades, None, bench)


def test_pairs_need_a_benchmark_and_the_trade_floor(tmp_path):
    store = CrossStockStore(db_path=str(tmp_path / "x.db"))
    _fill(store, "a", [(0.5, 0.2, MIN_TRADES_FLOOR), (0.1, None, MIN_TRADES_FLOOR), (0.3, 0.1, MIN_TRADES_FLOOR - 1)])
    assert store.get_pair_returns_by_strategy() == {"a": [(0.5, 0.2)]}


def test_non_causal_strategies_are_excluded(tmp_path):
    store = CrossStockStore(db_path=str(tmp_path / "x.db"))
    _fill(store, "leaky", [(9.0, 0.2, 50)])
    _fill(store, "clean", [(0.3, 0.2, 50)])
    store.record_causality("leaky", False, 1, 5)
    assert set(store.get_pair_returns_by_strategy()) == {"clean"}


async def test_status_payload_carries_the_vs_benchmark_view(tmp_path, monkeypatch):
    import plugins.trading_replay as tr

    store = CrossStockStore(db_path=str(tmp_path / "x.db"))
    _fill(store, "edge", [(1.0 + 0.1 * (i % 2 == 0), 1.0, 50) for i in range(30)])
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "build_pairs", lambda specs, basket: [])
    monkeypatch.setattr(tr, "StrategyStore", lambda: type("S", (), {"list_all": lambda self: []})())
    monkeypatch.setattr(tr, "SettingsStore", lambda: type("T", (), {"get": lambda self, k: None})())
    data = json.loads(await tr.get_cross_stock_replay_status())
    assert data["vs_benchmark_ranked"] == 1
    row = data["top_vs_benchmark"][0]
    assert row["strategy_id"] == "edge" and row["stocks_tested"] == 30
    assert set(row) >= {"beat_share", "median_excess", "p_value", "q_value", "significant"}
    assert "Informational" in data["vs_benchmark_caveat"]
    assert "top_consistent" in data  # legacy field kept for older renderers
