"""Unit tests for cerebral/trading/cross_stock_replay.py (CROSS-STOCK-VALIDATION S2).

Replaces self_dev's own first attempt at this file (PR #1240): that attempt
never wrote an implementation module at all (only this test file), and its
mocks assumed a `bar_cache.BarCache` class that doesn't exist anywhere in
this codebase -- bar_cache.py is a plain module-level get_bars function
(see cerebral/trading/bar_cache.py), same as run_replay already uses.
"""
import pandas as pd
import pytest

from cerebral.trading.cross_stock_replay import build_pairs, run_pair
from cerebral.trading.strategy_store import StrategySpec

_ALWAYS_LONG = "def strategy(data):\n    return [1] * len(data)\n"


class FakeBarCache:
    """Same shape as test_replay.py's own FakeBarCache -- get_bars(symbol,
    start, end, interval), ignores the args, returns synthetic bars wide
    enough to cover warm-up plus the requested window."""
    def get_bars(self, symbol, start, end, interval):
        idx = pd.date_range(start="2021-01-01", periods=1400, freq="B")
        close = [100.0 + i * 0.05 for i in range(1400)]
        return pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * 1400},
            index=idx,
        )


class FailingBarCache:
    def get_bars(self, symbol, start, end, interval):
        raise ValueError("Missing columns in Alpaca response for " + symbol)


def test_run_pair_against_a_stock_other_than_the_strategys_own_symbol():
    """The whole point of this campaign: symbol is independent of the
    strategy's own registered spec.symbol."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="NVDA",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["flat_reason"] is None
    assert result["net_return"] is not None
    assert result["n_trades"] >= 0


def test_run_pair_reports_flat_reason_on_bar_fetch_failure():
    """A missing-bars failure (the exact class of error BATCH-REPLAY
    already tolerates for UBER/LYFT/etc.) must not raise -- it's recorded
    as a real flat_reason, with no fabricated numeric result."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="XYZ",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FailingBarCache(),
    )
    assert result["net_return"] is None
    assert result["max_drawdown"] is None
    assert result["flat_reason"] is not None


def test_run_pair_reports_flat_reason_on_broken_strategy_code():
    result = run_pair(
        strategy_id="s1", code="def strategy(data):\n    raise RuntimeError('kaboom')\n",
        symbol="AAPL", start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["flat_reason"] is not None
    assert "kaboom" in result["flat_reason"]


def test_build_pairs_excludes_stock_specific_and_unclassified_strategies():
    """Only cross_test_eligible=True specs enter the sweep -- False (stock-
    specific) and None (never classified) are both excluded, same
    conservative-refuse convention as check_graduation's own gates."""
    generic = StrategySpec("generic", "AAPL", _ALWAYS_LONG, cross_test_eligible=True)
    specific = StrategySpec("specific", "MSFT", _ALWAYS_LONG, cross_test_eligible=False)
    unclassified = StrategySpec("unclassified", "NVDA", _ALWAYS_LONG)  # cross_test_eligible=None

    pairs = build_pairs([generic, specific, unclassified], basket=["AAPL", "MSFT"])

    assert pairs == [(generic, "AAPL"), (generic, "MSFT")]


def test_build_pairs_is_cross_product_of_eligible_specs_and_basket():
    a = StrategySpec("a", "AAPL", _ALWAYS_LONG, cross_test_eligible=True)
    b = StrategySpec("b", "MSFT", _ALWAYS_LONG, cross_test_eligible=True)

    pairs = build_pairs([a, b], basket=["X", "Y", "Z"])

    assert len(pairs) == 6
    assert (a, "X") in pairs and (b, "Z") in pairs
