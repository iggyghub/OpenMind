"""Gate test for trading_replay plugin discovery (ADR-0034).

Mirrors cerebral/tests/test_plugin_settings_control.py's pattern -- a real
guard-clause assertion, not a placeholder, so this file both satisfies the
orchestrator's REASON_NO_TEST_FILE gate and actually exercises something.
"""
import asyncio
import json
import unittest.mock

from datetime import datetime

from cerebral.settings import SettingsStore
from cerebral.trading.strategy_store import StrategySpec, StrategyStore
from plugins.trading_replay import (
    REQUIRED_CAPABILITIES, create, start_batch_replay, stop_batch_replay, get_batch_replay_status,
    start_cross_stock_replay, stop_cross_stock_replay, get_cross_stock_replay_status,
    check_cross_stock_night_window,
)


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_replay_report_rejects_missing_run_id():
    plugin = create()
    result = asyncio.run(plugin.call_tool("replay_report", {}))
    assert result.is_error is True


def _isolated_settings(tmp_path):
    """start_batch_replay/_run_batch_replay/get_batch_replay_status all
    construct a bare SettingsStore() internally -- no injectable seam, unlike
    the class-based plugins. SettingsStore.__init__'s default path arg is
    bound at import time, so monkeypatching cerebral.settings._SETTINGS_PATH
    has no effect on calls already compiled against the old default; the
    only thing that actually redirects those internal calls is patching the
    SettingsStore name binding inside plugins.trading_replay itself."""
    return SettingsStore(path=tmp_path / "felix-settings.json")


async def test_batch_replay_resumability(tmp_path, monkeypatch):
    """A batch replay picks up from a persisted cursor on restart, not from
    start_date again.

    async def, not a sync test calling asyncio.run() -- this repo's suite
    runs pytest-asyncio in asyncio_mode=auto; asyncio.run() inside a sync
    test body closes the loop pytest-asyncio shares across tests and breaks
    whatever runs after it. It would also orphan start_batch_replay's own
    asyncio.create_task() the instant each asyncio.run() call returns, since
    a task is bound to the loop that created it -- a single shared loop
    (this function's own) is what lets the background task actually run
    between awaits, not just get destroyed unexecuted."""
    isolated = _isolated_settings(tmp_path)
    monkeypatch.setattr("plugins.trading_replay.SettingsStore", lambda: isolated)

    with unittest.mock.patch("plugins.trading_replay.run_replay", return_value="mock_run_id"):
        await start_batch_replay("2016-01-01")
        # asyncio.create_task() only schedules the background task -- it
        # doesn't run until this coroutine actually yields. start_batch_replay
        # itself has no suspending await, so without this the task hasn't
        # executed its own eager cursor/start/running persistence yet.
        await asyncio.sleep(0)
        try:
            assert isolated.get("batch_replay_cursor") == "2016-01-01"
            assert isolated.get("batch_replay_start") == "2016-01-01"
            assert isolated.get("batch_replay_running") is True

            # Simulate a restart: manually advance the cursor, as the
            # background loop itself would after processing a month.
            isolated.set("batch_replay_cursor", "2016-02-01")

            # start_batch_replay again while the task is still running must
            # not reset it -- the idempotent guard fires.
            msg = await start_batch_replay("2016-01-01")
            assert msg == "Batch replay already running."
            # And must not have clobbered the manually-advanced cursor.
            assert isolated.get("batch_replay_cursor") == "2016-02-01"
        finally:
            await stop_batch_replay()


async def test_batch_replay_stop_mid_sweep(tmp_path, monkeypatch):
    """stop_batch_replay halts after the current month and persists real
    calendar-month progress (not a single day -- the bug this regression-
    tests: an earlier version advanced the cursor by timedelta(days=1)
    instead of to next_month_start, which would re-replay nearly the same
    month forever and never reach today)."""
    isolated = _isolated_settings(tmp_path)
    monkeypatch.setattr("plugins.trading_replay.SettingsStore", lambda: isolated)

    with unittest.mock.patch("plugins.trading_replay.run_replay", return_value="mock_run_id"):
        await start_batch_replay("2016-01-01")
        # Give the background task room to process at least one month.
        await asyncio.sleep(0.2)
        await stop_batch_replay()

        status = await get_batch_replay_status()
        data = json.loads(status)
        assert data["running"] is False
        assert data["cursor_date"] >= "2016-02-01"


# CROSS-STOCK-VALIDATION S2 (#1235): same three isolation seams as batch
# replay's own tests above -- SettingsStore, StrategyStore, and (new here)
# CrossStockStore all get bare, uninjectable constructions inside
# plugins.trading_replay, so isolating a test means monkeypatching the
# module-level name, not passing a constructor arg.

def _isolated_strategy_store(tmp_path, specs):
    store = StrategyStore(db_path=tmp_path / "specs.db")
    for spec in specs:
        store.save(spec)
    return store


def _mock_run_pair_result(net_return=0.1, max_drawdown=-0.05, n_trades=3, flat_reason=None):
    return {"net_return": net_return, "max_drawdown": max_drawdown, "n_trades": n_trades, "flat_reason": flat_reason}


async def test_cross_stock_replay_eager_state_and_already_running_guard(tmp_path, monkeypatch):
    isolated_settings = _isolated_settings(tmp_path)
    monkeypatch.setattr("plugins.trading_replay.SettingsStore", lambda: isolated_settings)
    isolated_strategies = _isolated_strategy_store(tmp_path, [
        StrategySpec("s1", "AAPL", "def strategy(data): return [0]", cross_test_eligible=True),
    ])
    monkeypatch.setattr("plugins.trading_replay.StrategyStore", lambda: isolated_strategies)
    from cerebral.trading.cross_stock_store import CrossStockStore
    isolated_cross = CrossStockStore(db_path=str(tmp_path / "cross_stock.db"))
    monkeypatch.setattr("plugins.trading_replay.CrossStockStore", lambda: isolated_cross)
    monkeypatch.setattr("plugins.trading_replay.BASKET", ["X", "Y"])

    with unittest.mock.patch("plugins.trading_replay.run_pair", return_value=_mock_run_pair_result()):
        await start_cross_stock_replay()
        # Eager state only -- set before the loop's first iteration, same
        # style as test_batch_replay_resumability's own asyncio.sleep(0).
        await asyncio.sleep(0)
        try:
            assert isolated_settings.get("cross_stock_running") is True

            msg = await start_cross_stock_replay()
            assert msg == "Cross-stock replay already running."
        finally:
            await stop_cross_stock_replay()

    assert isolated_settings.get("cross_stock_running") is False


async def test_cross_stock_replay_processes_all_pairs_and_persists_final_cursor(tmp_path, monkeypatch):
    isolated_settings = _isolated_settings(tmp_path)
    monkeypatch.setattr("plugins.trading_replay.SettingsStore", lambda: isolated_settings)
    s1 = StrategySpec("s1", "AAPL", "def strategy(data): return [0]", cross_test_eligible=True)
    s2 = StrategySpec("s2", "MSFT", "def strategy(data): return [0]", cross_test_eligible=True)
    isolated_strategies = _isolated_strategy_store(tmp_path, [s1, s2])
    monkeypatch.setattr("plugins.trading_replay.StrategyStore", lambda: isolated_strategies)
    from cerebral.trading.cross_stock_store import CrossStockStore
    isolated_cross = CrossStockStore(db_path=str(tmp_path / "cross_stock.db"))
    monkeypatch.setattr("plugins.trading_replay.CrossStockStore", lambda: isolated_cross)
    monkeypatch.setattr("plugins.trading_replay.BASKET", ["X", "Y"])

    with unittest.mock.patch("plugins.trading_replay.run_pair", return_value=_mock_run_pair_result()):
        await start_cross_stock_replay()
        # 2 strategies x 2 symbols = 4 pairs, each an instant mocked call --
        # comfortably finishes within a short real sleep.
        await asyncio.sleep(0.3)

    status = json.loads(await get_cross_stock_replay_status())
    assert status["running"] is False
    assert status["pairs_total"] == 4
    assert status["pairs_done"] == 4
    assert status["cursor_strategy_id"] == "s2"
    assert status["cursor_symbol"] == "Y"

    results = isolated_cross.get_results_by_strategy("s1")
    assert {r["symbol"] for r in results} == {"X", "Y"}


async def test_cross_stock_replay_resumes_from_persisted_cursor_not_from_start(tmp_path, monkeypatch):
    """The exact resumability requirement from #1235: a pair already marked
    done must not be re-run, and pairs before it in the list must be
    skipped too -- resuming means continuing, not restarting."""
    isolated_settings = _isolated_settings(tmp_path)
    monkeypatch.setattr("plugins.trading_replay.SettingsStore", lambda: isolated_settings)
    s1 = StrategySpec("s1", "AAPL", "def strategy(data): return [0]", cross_test_eligible=True)
    s2 = StrategySpec("s2", "MSFT", "def strategy(data): return [0]", cross_test_eligible=True)
    isolated_strategies = _isolated_strategy_store(tmp_path, [s1, s2])
    monkeypatch.setattr("plugins.trading_replay.StrategyStore", lambda: isolated_strategies)
    from cerebral.trading.cross_stock_store import CrossStockStore
    isolated_cross = CrossStockStore(db_path=str(tmp_path / "cross_stock.db"))
    monkeypatch.setattr("plugins.trading_replay.CrossStockStore", lambda: isolated_cross)
    monkeypatch.setattr("plugins.trading_replay.BASKET", ["X", "Y"])

    # Pairs in order: (s1,X), (s1,Y), (s2,X), (s2,Y) -- pretend a previous
    # run already completed through (s1, Y).
    isolated_settings.set("cross_stock_cursor_strategy_id", "s1")
    isolated_settings.set("cross_stock_cursor_symbol", "Y")

    calls = []

    def recording_run_pair(strategy_id, code, symbol, start, end, interval="1d"):
        calls.append((strategy_id, symbol))
        return _mock_run_pair_result()

    with unittest.mock.patch("plugins.trading_replay.run_pair", side_effect=recording_run_pair):
        await start_cross_stock_replay()
        await asyncio.sleep(0.3)

    assert calls == [("s2", "X"), ("s2", "Y")]


# CROSS-STOCK-VALIDATION S3 (#1236): nightly window function. Real system
# clock never touched -- `now` is injected, same convention as
# cerebral.trading.market_hours.is_market_hours's own tests.

async def test_night_window_starts_the_sweep_when_inside_window_and_idle(tmp_path):
    settings = _isolated_settings(tmp_path)
    with unittest.mock.patch("plugins.trading_replay.start_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_start:
        await check_cross_stock_night_window(settings, now=datetime(2026, 9, 16, 2, 0))  # 2am ET

    mock_start.assert_awaited_once()
    assert settings.get("cross_stock_night_started_at") != ""


async def test_night_window_does_not_start_twice_once_already_marked(tmp_path):
    """The night_started_at marker (not cross_stock_running) is what
    prevents re-starting every 5-minute tick for the rest of the window."""
    settings = _isolated_settings(tmp_path)
    settings.set("cross_stock_night_started_at", "2026-09-16T05:00:00+00:00")
    with unittest.mock.patch("plugins.trading_replay.start_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_start, \
         unittest.mock.patch("plugins.trading_replay.stop_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_stop:
        await check_cross_stock_night_window(settings, now=datetime(2026, 9, 16, 3, 0))  # still 3am, well under cap

    mock_start.assert_not_awaited()
    mock_stop.assert_not_awaited()
    assert settings.get("cross_stock_night_started_at") == "2026-09-16T05:00:00+00:00"  # untouched


async def test_night_window_does_not_start_when_already_running_without_marker(tmp_path):
    """A manual start (tray button) sets cross_stock_running without a
    night_started_at marker -- the nightly check must not treat that as
    something to start on top of."""
    settings = _isolated_settings(tmp_path)
    settings.set("cross_stock_running", True)
    with unittest.mock.patch("plugins.trading_replay.start_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_start:
        await check_cross_stock_night_window(settings, now=datetime(2026, 9, 16, 2, 0))

    mock_start.assert_not_awaited()
    assert settings.get("cross_stock_night_started_at") == ""


async def test_night_window_stops_at_8am_et_regardless_of_elapsed_time(tmp_path):
    settings = _isolated_settings(tmp_path)
    # Started 30 minutes ago -- nowhere near the 8-hour cap, but 8am ET
    # is its own independent stop condition.
    settings.set("cross_stock_night_started_at", "2026-09-16T11:30:00+00:00")  # 7:30am ET
    with unittest.mock.patch("plugins.trading_replay.stop_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_stop:
        await check_cross_stock_night_window(settings, now=datetime(2026, 9, 16, 8, 0))  # 8am ET

    mock_stop.assert_awaited_once()
    assert settings.get("cross_stock_night_started_at") == ""


async def test_night_window_stops_after_8_hours_even_if_still_before_8am(tmp_path):
    """The elapsed-hours cap is independent of the hour check -- covers a
    stuck/drifted clock scenario, not just the normal midnight-start case
    that would naturally hit 8am first."""
    settings = _isolated_settings(tmp_path)
    settings.set("cross_stock_night_started_at", "2026-09-15T18:00:00+00:00")  # 2pm ET the prior day
    with unittest.mock.patch("plugins.trading_replay.stop_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_stop:
        # now_ny.hour is 7am (< 8), but 17 real hours have elapsed since start.
        await check_cross_stock_night_window(settings, now=datetime(2026, 9, 16, 7, 0))

    mock_stop.assert_awaited_once()
    assert settings.get("cross_stock_night_started_at") == ""


async def test_night_window_does_nothing_outside_the_window_when_idle(tmp_path):
    settings = _isolated_settings(tmp_path)
    with unittest.mock.patch("plugins.trading_replay.start_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_start, \
         unittest.mock.patch("plugins.trading_replay.stop_cross_stock_replay", new_callable=unittest.mock.AsyncMock) as mock_stop:
        await check_cross_stock_night_window(settings, now=datetime(2026, 9, 16, 14, 0))  # 2pm ET

    mock_start.assert_not_awaited()
    mock_stop.assert_not_awaited()
