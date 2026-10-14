"""Tests for cross_stock_universe: BASKET integrity and is_stock_specific heuristic."""
from cerebral.trading.cross_stock_universe import BASKET, classify_all_strategies, is_stock_specific
from cerebral.trading.strategy_store import StrategySpec, StrategyStore


def test_basket_count_and_deduplication():
    """The campaign requires exactly 100 unique, deduplicated symbols."""
    assert len(BASKET) == 100
    assert len(set(BASKET)) == 100


def test_is_stock_specific_generic_strategy():
    """A purely technical description should be eligible (not stock-specific)."""
    spec = StrategySpec(
        strategy_id="generic-rsi",
        symbol="AAPL",
        code="def strategy(data):\n    return 1 if data.rsi < 30 else 0\n",
    )
    assert is_stock_specific(spec) is False


def test_classify_all_strategies_classifies_only_unclassified(tmp_path):
    """S1's 4th deliverable: a one-shot pass over every registered strategy,
    idempotent -- only touches specs still at their None default."""
    store = StrategyStore(db_path=tmp_path / "specs.db")
    store.save(StrategySpec("generic", "AAPL", "def strategy(data):\n    return 1 if data.rsi < 30 else 0\n"))
    store.save(StrategySpec("specific", "MSFT", "def strategy(data):\n    # Only trade MSFT\n    return 0\n"))
    store.save(StrategySpec(
        "already-done", "NVDA", "def strategy(data):\n    return 0\n",
        cross_test_eligible=False,
    ))

    classified = classify_all_strategies(store)

    assert classified == 2  # "already-done" was skipped
    assert store.get("generic").cross_test_eligible is True
    assert store.get("specific").cross_test_eligible is False
    assert store.get("already-done").cross_test_eligible is False  # untouched, not re-derived

    # Re-running is a no-op -- everything's classified now.
    assert classify_all_strategies(store) == 0


def test_is_stock_specific_naming_ticker():
    """Explicitly naming a ticker from the basket makes it stock-specific."""
    spec = StrategySpec(
        strategy_id="buy-msft-only",
        symbol="MSFT",
        code="def strategy(data):\n    # Only trade MSFT\n    return 1 if data.close > 300 else 0\n",
    )
    assert is_stock_specific(spec) is True


def test_is_stock_specific_naming_basket_composition():
    """Mentioning a basket of specific stocks should classify as stock-specific."""
    spec = StrategySpec(
        strategy_id="dow-high-div",
        symbol="DIA",
        code="def strategy(data):\n    # Select the five Dow stocks with highest dividend yield\n    tickers = ['AAPL', 'MSFT', 'JPM', 'V', 'JNJ']\n    return 0\n",
    )
    assert is_stock_specific(spec) is True


def test_is_stock_specific_unknown_ticker():
    """Mentioning a ticker not in the basket (or the strategy's own symbol) does not trigger stock-specific."""
    spec = StrategySpec(
        strategy_id="generic-macro",
        symbol="SPY",
        code="def strategy(data):\n    # Trade based on interest rates\n    return 1 if data.rate > 0.05 else 0\n",
    )
    assert is_stock_specific(spec) is False
