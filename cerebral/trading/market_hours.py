"""US equities regular-session gate for paper-trade dispatch (S31/#896).

Pure and duck-typed, like live_tick.py/discovery.py -- takes `now` as an
optional injected argument so tests don't have to wait for or fake the
real system clock via monkeypatching.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo("America/New_York")


def is_market_hours(now: Optional[datetime] = None) -> bool:
    """Mon-Fri 09:30-16:00 America/New_York, stdlib zoneinfo only (decision
    #22 -- free only, no new dependency).

    Known, disclosed limitation: no market-holiday calendar --
    Thanksgiving/Christmas/etc. will incorrectly read as open. A real NYSE
    holiday calendar is a separate slice if it ever matters; this gate's
    job is just "not literally 24/7," which is what was actually asked for.

    A naive `now` (no tzinfo) is treated as already being NY-local time
    rather than converted via the system's own locale -- so a test can
    build one directly (`datetime(2026, 8, 25, 10, 0)`) without also
    fighting the test runner's own timezone.
    """
    if now is None:
        ny_now = datetime.now(_NY_TZ)
    elif now.tzinfo is None:
        ny_now = now.replace(tzinfo=_NY_TZ)
    else:
        ny_now = now.astimezone(_NY_TZ)
    if ny_now.weekday() >= 5:  # Saturday, Sunday
        return False
    minutes_since_midnight = ny_now.hour * 60 + ny_now.minute
    return 9 * 60 + 30 <= minutes_since_midnight < 16 * 60
