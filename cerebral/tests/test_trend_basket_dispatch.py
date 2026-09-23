"""Tests for TradingStrategiesPlugin._trend_basket_dispatch (ADR-0038 / #1344).

Hand-added: the self_dev PR that generated this slice put the whole implementation in a
wrong-location duplicate module that was never actually wired to anything (the real
plugins/trading_strategies.py was untouched), called build_dynamic_universe() with no
arguments, never imported TREND1's real strategy code or TREND2's real gate/ranking
functions, and crashed cerebral.main on import via a bad top-level call. These tests cover
the hand-rewritten real implementation against what the issue's own acceptance criteria asked
for: rising-edge gating, top-10 dispatch via the real per-symbol Gauntlet, skipping an
already-held symbol, and activity logging.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from plugins.trading_strategies import TradingStrategiesPlugin
from cerebral.mcp.orchestrator import ToolResult
from cerebral.settings import SettingsStore
from cerebral.trading.strategy_store import StrategyStore, mint_expansion_strategy_id
from cerebral.trading.trend_basket_strategy import TREND_BASKET_STRATEGY_CODE


def _plugin(tmp_path):
    return TradingStrategiesPlugin(settings=SettingsStore(path=tmp_path / "felix-settings.json"))


def _raising_broker():
    """A broker whose calls all fail, so build_dynamic_universe falls open to its own
    deterministic _KNOWN_TICKERS fallback rather than needing a full mock data shape."""
    broker = MagicMock()
    broker.get_market_movers.side_effect = RuntimeError("no live data in tests")
    broker.get_most_actives.side_effect = RuntimeError("no live data in tests")
    broker.get_all_assets.side_effect = RuntimeError("no live data in tests")
    return broker


def _uptrend_bars(n=60, start_close=100.0, daily_gain=0.01):
    closes = [start_close * (1 + daily_gain) ** i for i in range(n)]
    return pd.DataFrame(
        {"Open": closes, "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
         "Close": closes, "Volume": [500_000] * n},
        index=pd.date_range(end=datetime.now(timezone.utc).date(), periods=n, freq="D"),
    )


def _flat_bars(n=60, close=100.0):
    return pd.DataFrame(
        {"Open": [close] * n, "High": [close] * n, "Low": [close] * n,
         "Close": [close] * n, "Volume": [500_000] * n},
        index=pd.date_range(end=datetime.now(timezone.utc).date(), periods=n, freq="D"),
    )


def _fetch_all_uptrend(symbol, start, end, interval="1d"):
    return _uptrend_bars()


def _fetch_all_flat(symbol, start, end, interval="1d"):
    return _flat_bars()


def _store_never_held():
    store = MagicMock(spec=StrategyStore)
    store.get.return_value = None
    return store


@pytest.mark.asyncio
async def test_not_a_rising_edge_no_dispatch(tmp_path):
    """Flat/low breadth (every symbol at/below its own MA on the very first reading) never
    crosses the 60% threshold, so this is not a rising edge and nothing dispatches."""
    plugin = _plugin(tmp_path)
    store = _store_never_held()

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("gauntlet must not run when the gate is not a rising edge")
    plugin._run_gauntlet = should_not_be_called

    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_flat, broker=_raising_broker(),
    )
    data = json.loads(result.content)
    assert data["dispatched"] == []
    assert data["rising_edge"] is False


@pytest.mark.asyncio
async def test_rising_edge_dispatches_top_candidates_via_real_gauntlet(tmp_path):
    """Uptrending data for every candidate puts breadth well above 60% on the first-ever
    reading (off -> on is a rising edge), which dispatches up to 10 candidates, each through
    _run_gauntlet with TREND1's real strategy code."""
    plugin = _plugin(tmp_path)
    store = _store_never_held()

    dispatched = []
    async def capture_gauntlet(args, **kwargs):
        dispatched.append((args["code"], args["symbol"], kwargs.get("strategy_id")))
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = capture_gauntlet

    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    data = json.loads(result.content)
    assert data["rising_edge"] is True
    assert 1 <= len(dispatched) <= 10
    assert all(code == TREND_BASKET_STRATEGY_CODE for code, _, _ in dispatched)
    for _, symbol, strategy_id in dispatched:
        assert strategy_id == mint_expansion_strategy_id(
            "Trend basket: breadth-gated momentum x volatility basket (ADR-0038)", symbol,
        )


@pytest.mark.asyncio
async def test_already_held_candidate_is_skipped(tmp_path):
    plugin = _plugin(tmp_path)
    held_symbol = "AAPL"
    held_id = mint_expansion_strategy_id(
        "Trend basket: breadth-gated momentum x volatility basket (ADR-0038)", held_symbol,
    )
    store = MagicMock(spec=StrategyStore)
    store.get.side_effect = lambda sid: object() if sid == held_id else None

    dispatched = []
    async def capture_gauntlet(args, **kwargs):
        dispatched.append(args["symbol"])
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = capture_gauntlet

    await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    assert held_symbol not in dispatched


@pytest.mark.asyncio
async def test_activity_log_records_dispatched_symbols_and_verdicts(tmp_path):
    plugin = _plugin(tmp_path)
    store = _store_never_held()

    async def always_validated(args, **kwargs):
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    plugin._run_gauntlet = always_validated

    logged = []
    async def record_activity(kind, payload):
        logged.append((kind, payload))
    plugin._record_activity_fn = record_activity

    await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    assert len(logged) == 1
    kind, payload = logged[0]
    assert payload["source"] == "trend_basket_dispatch"
    assert payload["symbols"]
    assert set(payload["verdicts"].keys()) == set(payload["symbols"])


@pytest.mark.asyncio
async def test_candidate_pool_build_failure_is_graceful(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    store = _store_never_held()

    def raise_build(*args, **kwargs):
        raise RuntimeError("simulated total pool-build failure")
    monkeypatch.setattr("plugins.trading_strategies.build_dynamic_universe", raise_build)

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("gauntlet must not run when the candidate pool never built")
    plugin._run_gauntlet = should_not_be_called

    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    data = json.loads(result.content)
    assert data["dispatched"] == []
    assert "reason" in data


@pytest.mark.asyncio
async def test_reentrancy_guard_refuses_overlapping_dispatch(tmp_path):
    """A dispatch already in flight refuses a second overlapping call rather than letting two
    (redundant, network-heavy) passes run concurrently -- ADR-0028 rule 5, same reasoning as
    self_dev_campaign's own _campaign_running guard."""
    plugin = _plugin(tmp_path)
    store = _store_never_held()

    async def should_not_run(*args, **kwargs):
        raise AssertionError("the real dispatch body must not run while one is already in flight")
    plugin._trend_basket_dispatch_inner = should_not_run

    plugin._trend_basket_dispatch_running = True
    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    data = json.loads(result.content)
    assert data["dispatched"] == []
    assert "already running" in data["reason"]


@pytest.mark.asyncio
async def test_reentrancy_guard_clears_after_completion_even_on_error(tmp_path, monkeypatch):
    """The running flag must reset (in a finally) even when the dispatch itself raises, so one
    failed call doesn't permanently wedge every future tick into refusing to run."""
    plugin = _plugin(tmp_path)
    store = _store_never_held()

    def raise_build(*args, **kwargs):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr("plugins.trading_strategies.build_dynamic_universe", raise_build)

    await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    assert plugin._trend_basket_dispatch_running is False

    # A second call right after must actually run (not be refused as "still running").
    ran = {"called": False}
    async def mark_ran(*args, **kwargs):
        ran["called"] = True
        return ToolResult(content=json.dumps({"dispatched": [], "reason": "ok"}))
    plugin._trend_basket_dispatch_inner = mark_ran
    await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    assert ran["called"] is True
