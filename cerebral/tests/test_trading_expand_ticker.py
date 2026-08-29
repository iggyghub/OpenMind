"""Tests for SchedulerPlugin._expand_strategy_ticker (S42/#936).

Hand-added: the self_dev PR that generated this method shipped zero test
coverage and multiple real bugs (fake StrategySpec attributes that always
evaluated confidence to 0, a rank_for_day_trading call missing its
required fetch_ohlcv_fn argument, a strategy_id suffix format that didn't
match S39's mint_expansion_strategy_id convention, and a strategy.hypothesis
attribute access that doesn't exist on StrategySpec). These tests cover
what the issue's own acceptance criteria asked for.
"""
import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from plugins.scheduler import SchedulerPlugin
from cerebral.trading.strategy_store import StrategyStore, mint_expansion_strategy_id


def _plugin(tmp_path):
    return SchedulerPlugin(db_path=str(tmp_path / "sched.db"))


def _good_bars():
    """Liquid enough to clear rank_for_day_trading's filters for every symbol."""
    return pd.DataFrame(
        {"Open": [100.0] * 25, "High": [102.0] * 25, "Low": [98.0] * 25,
         "Close": [100.0] * 25, "Volume": [200_000] * 25},
        index=pd.date_range("2026-01-01", periods=25, freq="D"),
    )


def _fetch_all_liquid(symbol, start, end, interval="1d"):
    return _good_bars()


def _store_with(strategy_id, symbol, code="def strategy(d): pass", hypothesis="test hypothesis"):
    store = MagicMock(spec=StrategyStore)
    store.get.side_effect = lambda sid: MagicMock(symbol=symbol, code=code) if sid == strategy_id else None
    store.get_current_version.side_effect = lambda sid: {"hypothesis": hypothesis} if sid == strategy_id else None
    return store


@pytest.mark.asyncio
async def test_missing_strategy_id_is_rejected(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin._expand_strategy_ticker({})
    assert result.is_error is True
    assert "strategy_id" in result.content


@pytest.mark.asyncio
async def test_unknown_strategy_is_rejected(tmp_path):
    plugin = _plugin(tmp_path)
    store = _store_with("real_strategy", "AAPL")
    result = await plugin._expand_strategy_ticker({"strategy_id": "nonexistent"}, strategy_store=store)
    assert result.is_error is True
    assert "No strategy" in result.content


@pytest.mark.asyncio
async def test_non_positive_confidence_is_rejected_and_never_dispatches(tmp_path):
    plugin = _plugin(tmp_path)
    store = _store_with("my_claim", "AAPL")

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("gauntlet must not run for an ineligible strategy")
    plugin._run_gauntlet = should_not_be_called

    result = await plugin._expand_strategy_ticker(
        {"strategy_id": "my_claim"}, strategy_store=store, confidence_fn=lambda sid: 0.0,
    )
    assert result.is_error is True
    assert "non-positive confidence weight" in result.content


@pytest.mark.asyncio
async def test_eligible_strategy_dispatches_ranked_capped_candidates(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._settings.set("discovery_candidate_limit", 2)
    store = _store_with("my_claim", "AAPL")

    dispatched = []
    async def capture_gauntlet(args, **kwargs):
        dispatched.append((args["symbol"], kwargs.get("strategy_id")))
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = capture_gauntlet

    result = await plugin._expand_strategy_ticker(
        {"strategy_id": "my_claim"}, strategy_store=store,
        fetch=_fetch_all_liquid, confidence_fn=lambda sid: 1.0,
    )

    assert result.is_error is not True
    assert len(dispatched) == 2  # capped at discovery_candidate_limit
    assert all(symbol != "AAPL" for symbol, _ in dispatched)  # current symbol excluded


@pytest.mark.asyncio
async def test_new_strategy_id_uses_s39_suffix_convention(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._settings.set("discovery_candidate_limit", 1)
    store = _store_with("my_claim", "AAPL")

    dispatched_ids = []
    async def capture_gauntlet(args, **kwargs):
        dispatched_ids.append(kwargs.get("strategy_id"))
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = capture_gauntlet

    await plugin._expand_strategy_ticker(
        {"strategy_id": "my_claim"}, strategy_store=store,
        fetch=_fetch_all_liquid, confidence_fn=lambda sid: 1.0,
    )

    assert len(dispatched_ids) == 1
    expected = mint_expansion_strategy_id("my_claim", dispatched_ids[0].rsplit("@", 1)[-1])
    assert dispatched_ids[0] == expected
    assert " @" in dispatched_ids[0]  # S39's convention: a space before @SYMBOL


@pytest.mark.asyncio
async def test_original_strategy_row_is_never_touched(tmp_path):
    """The original symbol's spec/version lookups must only ever be read,
    never re-saved under the bare strategy_id -- expansion dispatches use
    the new @SYMBOL-suffixed id exclusively."""
    plugin = _plugin(tmp_path)
    plugin._settings.set("discovery_candidate_limit", 1)
    store = _store_with("my_claim", "AAPL")

    async def capture_gauntlet(args, **kwargs):
        assert kwargs.get("strategy_id") != "my_claim"
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = capture_gauntlet

    await plugin._expand_strategy_ticker(
        {"strategy_id": "my_claim"}, strategy_store=store,
        fetch=_fetch_all_liquid, confidence_fn=lambda sid: 1.0,
    )
    store.save.assert_not_called()


@pytest.mark.asyncio
async def test_one_candidate_failure_does_not_abort_the_batch(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._settings.set("discovery_candidate_limit", 2)
    store = _store_with("my_claim", "AAPL")

    call_count = 0
    async def flaky_gauntlet(args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated dispatch crash")
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = flaky_gauntlet

    result = await plugin._expand_strategy_ticker(
        {"strategy_id": "my_claim"}, strategy_store=store,
        fetch=_fetch_all_liquid, confidence_fn=lambda sid: 1.0,
    )

    data = json.loads(result.content)
    assert len(data["attempts"]) == 2  # both recorded, not just the one that didn't crash
    verdicts = [a["verdict"] for a in data["attempts"]]
    assert "ERROR" in verdicts
    assert "VALIDATED" in verdicts
