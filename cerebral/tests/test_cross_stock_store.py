import pytest

from cerebral.trading.cross_stock_store import CrossStockStore


@pytest.fixture
def store(tmp_path) -> CrossStockStore:
    return CrossStockStore(db_path=str(tmp_path / "cross_stock_results.db"))


def test_create_run_and_record_result(store: CrossStockStore) -> None:
    run_id = store.create_run("2021-09-15", "2026-09-15")
    assert run_id is not None
    assert len(run_id) == 32

    store.record_result(run_id, "strat_1", "AAPL", net_return=0.15, max_drawdown=-0.12, n_trades=10)
    store.record_result(run_id, "strat_1", "MSFT", net_return=-0.05, max_drawdown=-0.20, n_trades=8)

    results = store.get_results_by_strategy("strat_1")
    assert len(results) == 2
    aapl = next(r for r in results if r["symbol"] == "AAPL")
    assert aapl["net_return"] == 0.15
    assert aapl["max_drawdown"] == -0.12
    assert aapl["n_trades"] == 10
    assert aapl["flat_reason"] is None


def test_record_result_flat_reason_on_failure(store: CrossStockStore) -> None:
    """A pair that couldn't run (bad bars, sandbox failure) records a real
    flat_reason with None numeric fields, never a fabricated 0.0."""
    run_id = store.create_run("2021-09-15", "2026-09-15")
    store.record_result(run_id, "strat_1", "NFLX", net_return=None, max_drawdown=None,
                         n_trades=0, flat_reason="Missing columns in Alpaca response")

    results = store.get_results_by_strategy("strat_1")
    assert results[0]["net_return"] is None
    assert results[0]["flat_reason"] == "Missing columns in Alpaca response"


def test_record_result_upsert_overwrites_same_pair(store: CrossStockStore) -> None:
    """F1 (#1246): PK is now (strategy_id, symbol), so the same pair with a
    different run_id still upserts to a single row -- no duplicate-averaging."""
    run1 = store.create_run("2021-09-15", "2026-09-15")
    run2 = store.create_run("2021-09-15", "2026-09-15")
    store.record_result(run1, "strat_1", "AAPL", net_return=0.10, max_drawdown=-0.05, n_trades=5)
    store.record_result(run2, "strat_1", "AAPL", net_return=0.20, max_drawdown=-0.08, n_trades=7)

    results = store.get_results_by_strategy("strat_1")
    assert len(results) == 1  # not duplicated even across run_ids
    assert results[0]["net_return"] == 0.20
    assert results[0]["run_id"] == run2  # provenance updated to the latest run


def test_get_results_by_strategy_only_returns_that_strategy(store: CrossStockStore) -> None:
    run_id = store.create_run("2021-09-15", "2026-09-15")
    store.record_result(run_id, "strat_1", "AAPL", net_return=0.1, max_drawdown=-0.1, n_trades=1)
    store.record_result(run_id, "strat_2", "AAPL", net_return=0.2, max_drawdown=-0.2, n_trades=2)

    results = store.get_results_by_strategy("strat_1")
    assert len(results) == 1
    assert results[0]["strategy_id"] == "strat_1"


def test_get_tested_count_by_strategy_excludes_failed_pairs(store: CrossStockStore) -> None:
    """Same WHERE net_return IS NOT NULL as get_consistency_by_strategy --
    a consistency score must never be shown without the real sample size
    it's based on (S5/#1238)."""
    run_id = store.create_run("2021-09-15", "2026-09-15")
    store.record_result(run_id, "strat_1", "AAPL", net_return=0.1, max_drawdown=-0.1, n_trades=1)
    store.record_result(run_id, "strat_1", "MSFT", net_return=-0.1, max_drawdown=-0.1, n_trades=1)
    store.record_result(run_id, "strat_1", "UBER", net_return=None, max_drawdown=None, n_trades=0,
                         flat_reason="Missing columns in Alpaca response")

    counts = store.get_tested_count_by_strategy()

    assert counts["strat_1"] == 2  # not 3 -- the failed UBER pair doesn't count as "tested"


def test_get_tested_count_by_strategy_omits_strategies_with_zero_successful_pairs(store: CrossStockStore) -> None:
    run_id = store.create_run("2021-09-15", "2026-09-15")
    store.record_result(run_id, "strat_1", "AAPL", net_return=None, max_drawdown=None, n_trades=0,
                         flat_reason="Missing columns in Alpaca response")

    counts = store.get_tested_count_by_strategy()

    assert "strat_1" not in counts


def test_get_done_pairs_returns_all_recorded_pairs(store: CrossStockStore) -> None:
    """F1 (#1246): get_done_pairs is the resume skip-set -- includes both
    successful and failed pairs (a failed pair is still a tracked attempt)."""
    run_id = store.create_run("2021-09-15", "2026-09-15")
    store.record_result(run_id, "strat_1", "AAPL", net_return=0.1, max_drawdown=-0.05, n_trades=3)
    store.record_result(run_id, "strat_1", "MSFT", net_return=None, max_drawdown=None, n_trades=0,
                         flat_reason="Missing columns in Alpaca response")

    done = store.get_done_pairs()

    assert done == {("strat_1", "AAPL"), ("strat_1", "MSFT")}


def test_get_done_count_counts_all_rows(store: CrossStockStore) -> None:
    run_id = store.create_run("2021-09-15", "2026-09-15")
    assert store.get_done_count() == 0
    store.record_result(run_id, "strat_1", "AAPL", net_return=0.1, max_drawdown=-0.05, n_trades=3)
    store.record_result(run_id, "strat_1", "MSFT", net_return=None, max_drawdown=None, n_trades=0,
                         flat_reason="bad data")
    assert store.get_done_count() == 2
