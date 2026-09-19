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
