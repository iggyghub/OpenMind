import time
from typing import Optional

import pytest

from cerebral.trading.replay_store import ReplayStore


@pytest.fixture
def store(tmp_path) -> ReplayStore:
    db_path = str(tmp_path / "replay_runs.db")
    return ReplayStore(db_path=db_path)


def test_create_run_and_results(store: ReplayStore) -> None:
    run_id = store.create_run("2023-01-01", "2023-12-31", "1d", 5)
    assert run_id is not None
    assert len(run_id) == 32

    run = store.get_run(run_id)
    assert run is not None
    assert run["start"] == "2023-01-01"
    assert run["end"] == "2023-12-31"
    assert run["interval"] == "1d"
    assert run["n_strategies"] == 5

    store.record_result(run_id, "strat_1", "AAPL", 0.15, 0.12, 10, 0.05, 1.2, None)
    store.record_result(run_id, "strat_2", "GOOG", -0.05, -0.08, 5, 0.10, -0.8, "max_drawdown_hit")

    results = store.get_results(run_id)
    assert len(results) == 2

    r1 = next(r for r in results if r["strategy_id"] == "strat_1")
    r2 = next(r for r in results if r["strategy_id"] == "strat_2")

    assert r1["gross_return"] == 0.15
    assert r1["net_return"] == 0.12
    assert r1["n_trades"] == 10
    assert r1["max_drawdown"] == 0.05
    assert r1["sharpe"] == 1.2
    assert r1["flat_reason"] is None

    assert r2["gross_return"] == -0.05
    assert r2["net_return"] == -0.08
    assert r2["flat_reason"] == "max_drawdown_hit"


def test_list_runs_ordering_and_limit(store: ReplayStore) -> None:
    run_id_1 = store.create_run("2022-01-01", "2022-12-31", "1h", 2)
    time.sleep(0.01)
    run_id_2 = store.create_run("2023-01-01", "2023-12-31", "1d", 3)
    time.sleep(0.01)
    run_id_3 = store.create_run("2024-01-01", "2024-12-31", "1w", 1)

    runs = store.list_runs()
    assert len(runs) == 3
    assert runs[0]["run_id"] == run_id_3
    assert runs[1]["run_id"] == run_id_2
    assert runs[2]["run_id"] == run_id_1

    limited_runs = store.list_runs(limit=2)
    assert len(limited_runs) == 2
    assert limited_runs[0]["run_id"] == run_id_3
    assert limited_runs[1]["run_id"] == run_id_2


# BATCH-REPLAY S4 (#1227): worst-drawdown rollup feeding StrategyLifecycle's
# gates. max_drawdown is stored negative-or-zero (a min() accumulator over
# running peak-to-trough, cerebral/trading/replay.py:210) -- "worst" is the
# most negative value, MIN() not MAX().
def test_get_worst_drawdown_by_strategy_picks_min_across_all_runs(store: ReplayStore) -> None:
    run_1 = store.create_run("2023-01-01", "2023-01-31", "1d", 2)
    run_2 = store.create_run("2023-02-01", "2023-02-28", "1d", 2)

    store.record_result(run_1, "strat_a", "AAPL", 0.0, 0.0, 1, -0.05, 1.0, None)
    store.record_result(run_2, "strat_a", "AAPL", 0.0, 0.0, 1, -0.20, 1.0, None)
    store.record_result(run_1, "strat_b", "GOOG", 0.0, 0.0, 1, -0.10, 1.0, None)

    worst = store.get_worst_drawdown_by_strategy()
    assert worst["strat_a"] == -0.20  # min across both runs, not just the latest
    assert worst["strat_b"] == -0.10


def test_get_worst_drawdown_by_strategy_excludes_null_drawdowns(store: ReplayStore) -> None:
    run_id = store.create_run("2023-01-01", "2023-01-31", "1d", 1)
    store.record_result(run_id, "strat_flat", "AAPL", None, None, 0, None, None, "no_signal")

    worst = store.get_worst_drawdown_by_strategy()
    assert "strat_flat" not in worst


def test_get_worst_drawdown_by_strategy_empty_store_returns_empty_dict(store: ReplayStore) -> None:
    assert store.get_worst_drawdown_by_strategy() == {}
