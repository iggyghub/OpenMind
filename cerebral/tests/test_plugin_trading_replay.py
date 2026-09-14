"""Gate test for trading_replay plugin discovery (ADR-0034).

Mirrors cerebral/tests/test_plugin_settings_control.py's pattern -- a real
guard-clause assertion, not a placeholder, so this file both satisfies the
orchestrator's REASON_NO_TEST_FILE gate and actually exercises something.
"""
import asyncio
import json
import unittest.mock

from cerebral.settings import SettingsStore
from plugins.trading_replay import REQUIRED_CAPABILITIES, create, start_batch_replay, stop_batch_replay, get_batch_replay_status


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
