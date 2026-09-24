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
from cerebral.trading.trend_basket_strategy import entry_date_of, trend_basket_code


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


_CLAIM = "Trend basket: breadth-gated momentum x volatility basket (ADR-0038)"


class _FakeScheduler:
    """Just enough of SchedulerPlugin for event registration: _con + _create_event."""
    def __init__(self):
        import sqlite3
        self._con = sqlite3.connect(":memory:")
        self._con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT)")
        self.created = []

    def _create_event(self, args):
        self._con.execute("INSERT INTO events (title) VALUES (?)", (args["title"],))
        self.created.append(args["title"])


@pytest.mark.asyncio
async def test_rising_edge_registers_top_candidates_directly_without_gauntlet(tmp_path):
    """ADR-0038 amendment 2026-09-24: on a rising edge, up to 10 candidates are registered
    directly -- entry-dated strategy code, a recurring event each -- with no per-symbol Gauntlet."""
    plugin = _plugin(tmp_path)
    plugin._scheduler = _FakeScheduler()
    store = StrategyStore(db_path=tmp_path / "specs.db")

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("the per-symbol gauntlet must not run for the trend basket")
    plugin._run_gauntlet = should_not_be_called

    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    data = json.loads(result.content)
    assert data["rising_edge"] is True
    assert 1 <= len(data["dispatched"]) <= 10
    today = datetime.now(timezone.utc).date().isoformat()
    for r in data["dispatched"]:
        assert r["verdict"] == "REGISTERED"
        assert r["new_id"] == mint_expansion_strategy_id(_CLAIM, r["symbol"])
        spec = store.get(r["new_id"])
        assert entry_date_of(spec.code) == today
        assert spec.qty > 0
    assert sorted(plugin._scheduler.created) == sorted(r["new_id"] for r in data["dispatched"])


def _save_position(store, symbol, entry):
    from cerebral.trading.strategy_store import StrategySpec
    store.save(StrategySpec(mint_expansion_strategy_id(_CLAIM, symbol), symbol, trend_basket_code(entry), qty=1.0),
               origin="discovered")


def _today():
    return datetime.now(timezone.utc).date()


@pytest.mark.asyncio
async def test_a_position_entered_today_is_not_bought_again(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")
    _save_position(store, "AAPL", _today().isoformat())
    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    assert "AAPL" not in [r["symbol"] for r in json.loads(result.content)["dispatched"]]


def test_open_symbols_follow_each_position_s_own_exit_logic(tmp_path):
    """Occupied slot = the position's own strategy still says hold on the latest bars. A steady
    uptrend never trips the 12% trail, so a 5-day-old entry is still open; a flat-then-crash
    series trips it, so that slot is free; a 60-day-old entry is past the 20-day cap."""
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")
    five_days_ago = (_today() - timedelta(days=5)).isoformat()
    _save_position(store, "UP", five_days_ago)
    _save_position(store, "CRASH", five_days_ago)
    _save_position(store, "OLD", (_today() - timedelta(days=60)).isoformat())

    def fetch(symbol, start, end, interval="1d"):
        if symbol == "CRASH":
            df = _flat_bars()
            df.iloc[-2:, df.columns.get_loc("Low")] = 50.0  # -50%: past the 12% trail
            return df
        return _uptrend_bars()

    assert plugin._trend_basket_open_symbols(store, fetch) == {"UP"}


@pytest.mark.asyncio
async def test_active_regime_refills_only_the_empty_slots(tmp_path):
    """Refill (ADR-0038 amendment 2026-09-24): on a later day with the regime still active and 3
    positions still open, only the 7 empty slots are filled, never the held symbols."""
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")
    held = ["AAPL", "MSFT", "NVDA"]
    for sym in held:
        _save_position(store, sym, _today().isoformat())
    plugin._trend_basket_gate._active = True
    plugin._trend_basket_gate._last_breadth = 0.9
    plugin._trend_basket_gate._last_date = _today() - timedelta(days=1)

    result = await plugin._trend_basket_dispatch(
        {}, strategy_store=store, fetch=_fetch_all_uptrend, broker=_raising_broker(),
    )
    data = json.loads(result.content)
    assert data["active"] is True and data["rising_edge"] is False  # sustained, not a new cross
    symbols = [r["symbol"] for r in data["dispatched"]]
    assert not set(symbols) & set(held)
    assert 1 <= len(symbols) <= 7


def test_re_entry_does_not_duplicate_the_recurring_event(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._scheduler = _FakeScheduler()
    store = StrategyStore(db_path=tmp_path / "specs.db")
    plugin._register_trend_basket_position("AAPL", store, 100.0)
    plugin._register_trend_basket_position("AAPL", store, 100.0)
    assert len(plugin._scheduler.created) == 1


@pytest.mark.asyncio
async def test_activity_log_records_dispatched_symbols_and_verdicts(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

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
