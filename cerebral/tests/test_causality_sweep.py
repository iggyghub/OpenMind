import pytest
import types
from unittest.mock import MagicMock

from cerebral.trading.cross_stock_store import CrossStockStore
from plugins.trading_replay import _ensure_causality_checked, _CAUSALITY_REFERENCE_SYMBOL


pytestmark = pytest.mark.asyncio


def _make_spec(strategy_id, code="test_code", interval="1d", cross_test_eligible=True):
    return types.SimpleNamespace(
        strategy_id=strategy_id,
        code=code,
        interval=interval,
        cross_test_eligible=cross_test_eligible,
    )


DUMMY_BARS = []


@pytest.mark.asyncio
async def test_checks_once_and_skips_rechecks(tmp_path):
    store_path = str(tmp_path / "test.db")
    store = CrossStockStore(db_path=store_path)

    specs = [
        _make_spec("strat_1"),
        _make_spec("strat_2"),
    ]

    check_mock = MagicMock(return_value=MagicMock(causal=True, mismatches=0, tested=10))
    get_bars_mock = MagicMock(return_value=DUMMY_BARS)

    await _ensure_causality_checked(store, specs, get_bars=get_bars_mock, check=check_mock)

    assert store.get_causality_checked_ids() == {"strat_1", "strat_2"}
    assert check_mock.call_count == 2

    # Second call should check nothing
    check_mock.reset_mock()
    await _ensure_causality_checked(store, specs, get_bars=get_bars_mock, check=check_mock)
    assert check_mock.call_count == 0


@pytest.mark.asyncio
async def test_causal_false_vs_none(tmp_path):
    store_path = str(tmp_path / "test2.db")
    store = CrossStockStore(db_path=store_path)

    specs = [
        _make_spec("strat_false"),
        _make_spec("strat_none"),
    ]

    result_false = MagicMock(causal=False, mismatches=2, tested=10)
    result_none = MagicMock(causal=None, mismatches=0, tested=10)

    check_mock = MagicMock(side_effect=[result_false, result_none])
    get_bars_mock = MagicMock(return_value=DUMMY_BARS)

    await _ensure_causality_checked(store, specs, get_bars=get_bars_mock, check=check_mock)

    assert store.get_causality_checked_ids() == {"strat_false", "strat_none"}
    assert store.get_non_causal_ids() == {"strat_false"}


@pytest.mark.asyncio
async def test_get_bars_exception_skips_but_continues(tmp_path):
    store_path = str(tmp_path / "test3.db")
    store = CrossStockStore(db_path=store_path)

    specs = [
        _make_spec("strat_ok"),
        _make_spec("strat_fail"),
        _make_spec("strat_ok_2"),
    ]

    call_count = 0
    def failing_get_bars(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("DB error")
        return DUMMY_BARS

    check_mock = MagicMock(return_value=MagicMock(causal=True, mismatches=0, tested=5))
    get_bars_mock = failing_get_bars

    await _ensure_causality_checked(store, specs, get_bars=get_bars_mock, check=check_mock)

    # Only 2 strategies recorded (first and third)
    assert store.get_causality_checked_ids() == {"strat_ok", "strat_ok_2"}
    # First and third were checked, second was skipped due to exception
    assert check_mock.call_count == 2


@pytest.mark.asyncio
async def test_ineligible_specs_skipped(tmp_path):
    store_path = str(tmp_path / "test4.db")
    store = CrossStockStore(db_path=store_path)

    specs = [
        _make_spec("strat_eligible", cross_test_eligible=True),
        _make_spec("strat_ineligible", cross_test_eligible=False),
    ]

    check_mock = MagicMock(return_value=MagicMock(causal=True, mismatches=0, tested=5))
    get_bars_mock = MagicMock(return_value=DUMMY_BARS)

    await _ensure_causality_checked(store, specs, get_bars=get_bars_mock, check=check_mock)

    assert store.get_causality_checked_ids() == {"strat_eligible"}
    assert check_mock.call_count == 1


@pytest.mark.asyncio
async def test_a_leak_that_only_shows_on_a_stock_the_strategy_trades_on_is_caught(tmp_path):
    """Regression (2026-09-19): an AAPL-only check passed a strategy whose look-ahead only fires
    on high-volatility stocks. The check must also run where the strategy trades most."""
    store = CrossStockStore(db_path=str(tmp_path / "t.db"))
    run = store.create_run("2021-01-01", "2026-01-01")
    store.record_result(run, "s", "QUIET", 0.1, -0.1, 2, None, 0.1)
    store.record_result(run, "s", "LCID", 5.0, -0.3, 80, None, 0.1)
    store.record_result(run, "s", "RBLX", 2.0, -0.3, 60, None, 0.1)
    store.record_result(run, "s", "TINY", 0.1, -0.1, 1, None, 0.1)

    seen = []

    def fake_check(code, bars, n_cuts=60):
        seen.append((bars, n_cuts))
        leaky = bars == "LCID"
        return types.SimpleNamespace(causal=False if leaky else True, mismatches=1 if leaky else 0, tested=5)

    await _ensure_causality_checked(
        store, [_make_spec("s")], get_bars=lambda sym, a, b, i: sym, check=fake_check
    )
    assert [b for b, _ in seen][:2] == [_CAUSALITY_REFERENCE_SYMBOL, "LCID"]
    assert seen[0][1] == 60 and seen[1][1] == 30  # full cuts on the reference, fewer on the extras
    assert store.get_non_causal_ids() == {"s"}


@pytest.mark.asyncio
async def test_top_trade_symbols_are_ranked_by_trade_count(tmp_path):
    store = CrossStockStore(db_path=str(tmp_path / "t2.db"))
    run = store.create_run("2021-01-01", "2026-01-01")
    for sym, n in (("A", 5), ("B", 90), ("C", 40), ("D", 0)):
        store.record_result(run, "s", sym, 0.0, 0.0, n, None, 0.0)
    assert store.get_top_trade_symbols("s", 2) == ["B", "C"]
    assert store.get_top_trade_symbols("nope", 2) == []
