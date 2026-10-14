import types

import pytest

from cerebral.trading.cross_stock_store import CrossStockStore
from cerebral.trading.strategy_review import UNTRUSTED_TEXT, hold_untrusted_text_strategies

ROW = {"net_return": 0.1, "gross_return": 0.1, "benchmark_return": 0.0,
       "max_drawdown": -0.1, "benchmark_max_drawdown": -0.2, "n_trades": 4}
BAD = '<<<EXTERNAL_UNTRUSTED_CONTENT id="x">>>\nSource: Web Search\n---\nSome pasted page'


@pytest.fixture
def store(tmp_path):
    return CrossStockStore(db_path=str(tmp_path / "r.db"))


class FakeSpecs:
    def __init__(self, ids):
        self.ids, self.eligible, self.consistency = ids, {}, {}

    def list_all(self):
        return [types.SimpleNamespace(strategy_id=i) for i in self.ids]

    def update_cross_test_eligible(self, sid, v):
        self.eligible[sid] = v

    def update_cross_stock_consistency(self, sid, v):
        self.consistency[sid] = v


def test_untrusted_text_strategies_are_held_once_and_left_out_of_rankings(store):
    specs = FakeSpecs([BAD, "a real claim"])
    store.record_stress(BAD, "A", "gfc", ROW, 0.0002)
    store.record_stress("a real claim", "A", "gfc", ROW, 0.0002)
    store.record_permutation(BAD, "A", 0.5, 0.0, 0.01, 500, 0.0002)
    assert hold_untrusted_text_strategies(specs, store) == 1
    assert hold_untrusted_text_strategies(specs, store) == 0          # idempotent
    assert specs.eligible == {BAD: False} and specs.consistency == {BAD: None}
    assert [r["category"] for r in store.get_review_rows()] == [UNTRUSTED_TEXT]
    assert set(store.get_stress_rows()) == {"a real claim"}            # kept in the DB, out of the ranking
    assert store.get_permutation_pvalues() == {}
    assert BAD in store.get_excluded_ids()


def test_release_puts_a_strategy_back(store):
    store.set_review("s", UNTRUSTED_TEXT, "why")
    store.record_stress("s", "A", "gfc", ROW, 0.0002)
    assert store.get_stress_rows() == {}
    store.clear_review("s")
    assert set(store.get_stress_rows()) == {"s"}
