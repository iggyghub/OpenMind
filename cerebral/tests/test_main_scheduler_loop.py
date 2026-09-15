"""F2 (#1247): scheduler-loop safety tests -- check_cross_stock_night_window
must not block the singular scheduler when the cross-stock sweep is wedged.
ADR-0028 rule 5: a background job must not preempt or stall the scheduler.
"""
import asyncio
import datetime

import plugins.trading_replay as tr
from cerebral.settings import SettingsStore
from zoneinfo import ZoneInfo


async def test_check_cross_stock_night_window_completes_with_wedged_sweep(monkeypatch, tmp_path):
    """Scheduler tick must complete even when the sweep task never stops on its own."""
    wedged = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(tr, "_cross_stock_task", wedged)
    monkeypatch.setattr(tr, "_cross_stock_stop_flag", False)
    monkeypatch.setattr(tr, "_CROSS_STOCK_STOP_TIMEOUT_S", 0.05)

    settings = SettingsStore(tmp_path / "settings.json")
    ny_tz = ZoneInfo("America/New_York")
    now_ny = datetime.datetime(2026, 9, 15, 8, 0, tzinfo=ny_tz)
    now_utc = now_ny.astimezone(datetime.timezone.utc)
    # Signal an in-progress nightly run so the stop branch fires.
    settings.set("cross_stock_night_started_at", now_utc.isoformat())

    await tr.check_cross_stock_night_window(settings=settings, now=now_ny)

    assert wedged.cancelled()
