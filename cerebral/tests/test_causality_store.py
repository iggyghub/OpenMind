import pytest

from cerebral.trading.cross_stock_store import CrossStockStore
from cerebral.trading.cross_stock_replay import rollup_consistency


class FakeStrategyStore:
    """Minimal strategy store exposing only what rollup_consistency needs."""
    def __init__(self):
        self.consistency_values: dict[str, float | None] = {}

    def list_all(self):
        from unittest.mock import MagicMock
        return [
            MagicMock(strategy_id="causal_strat", interval="1d"),
            MagicMock(strategy_id="non_causal_strat", interval="1d"),
        ]

    def update_cross_stock_consistency(self, strategy_id: str, consistency: float | None) -> None:
        self.consistency_values[strategy_id] = consistency


@pytest.fixture
def store(tmp_path) -> CrossStockStore:
    return CrossStockStore(db_path=str(tmp_path / "x.db"))


def test_record_causality_round_trip(store: CrossStockStore):
    # True (causal)
    store.record_causality("causal_strat", True, mismatches=1, tested=10)
    assert store.get_causality_checked_ids() == {"causal_strat"}
    assert store.get_non_causal_ids() == set()

    # False (non-causal)
    store.record_causality("non_causal_strat", False, mismatches=2, tested=20)
    assert store.get_causality_checked_ids() == {"causal_strat", "non_causal_strat"}
    assert store.get_non_causal_ids() == {"non_causal_strat"}

    # None (untestable)
    store.record_causality("untestable_strat", None, mismatches=0, tested=0)
    assert store.get_causality_checked_ids() == {"causal_strat", "non_causal_strat", "untestable_strat"}
    assert store.get_non_causal_ids() == {"non_causal_strat"}  # None must not appear here

    # Re-recording overwrites
    store.record_causality("causal_strat", False, mismatches=5, tested=15)
    assert store.get_non_causal_ids() == {"non_causal_strat", "causal_strat"}
    assert store.get_causality_checked_ids() == {"causal_strat", "non_causal_strat", "untestable_strat"}


def test_rollup_consistency_skips_non_causal(store: CrossStockStore):
    fake_store = FakeStrategyStore()

    # Pre-mark one strategy as non-causal
    store.record_causality("non_causal_strat", False, mismatches=1, tested=5)

    # Mock get_consistency_by_strategy to return positive results for both
    original_get = store.get_consistency_by_strategy
    def mock_get(interval_by_strategy):
        return {
            "causal_strat": 0.8,
            "non_causal_strat": 0.9,  # Positive results, but should be cleared
        }
    store.get_consistency_by_strategy = mock_get

    rollup_consistency(fake_store, store)

    # Causal strategy gets its computed consistency
    assert fake_store.consistency_values["causal_strat"] == 0.8
    # Non-causal strategy is explicitly set to None (clears stale value)
    assert fake_store.consistency_values["non_causal_strat"] is None


def test_schema_creates_strategy_causality_table(store: CrossStockStore):
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(strategy_causality)")}
    assert cols == {"strategy_id", "causal", "mismatches", "tested", "checked_at"}


def test_rollup_clears_a_non_causal_strategy_that_has_no_result_rows(store: CrossStockStore):
    fake_store = FakeStrategyStore()
    store.record_causality("ghost", False, mismatches=3, tested=11)  # never swept
    rollup_consistency(fake_store, store)
    assert fake_store.consistency_values["ghost"] is None
