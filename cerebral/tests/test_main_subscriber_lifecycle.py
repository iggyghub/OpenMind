"""Issue #182: ``cerebral.main._start_discord_user_subscriber`` must not
let ``asyncio.CancelledError`` escape and kill the process.

``CancelledError`` inherits from ``BaseException`` on Python 3.8+, so the
wrapper's ``except Exception`` never saw it. A loop disturbance during
startup (e.g. ``openclaw_channels`` stdio teardown cancelling a sibling's
in-flight aiohttp request -- see #181) escaped all the way to
``asyncio.run`` and took Cerebral down. The wrapper must swallow + warn
for stray cancellations but re-raise during deliberate shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest


@pytest.fixture
def main_rig():
    """Patch ``cerebral.main._orc`` with a stub orchestrator and restore
    it (plus ``_shutdown`` state) afterwards."""
    import cerebral.main as main_mod

    saved_orc = main_mod._orc
    shutdown_was_set = main_mod._shutdown.is_set()
    main_mod._shutdown.clear()
    yield main_mod
    main_mod._orc = saved_orc
    if shutdown_was_set:
        main_mod._shutdown.set()
    else:
        main_mod._shutdown.clear()


def _orc_with_cancelling_start():
    async def start_subscriber() -> None:
        raise asyncio.CancelledError()

    module = SimpleNamespace(start_subscriber=start_subscriber)
    return SimpleNamespace(get_plugin_module=lambda name: module)


async def test_cancelled_start_is_swallowed_and_warned(main_rig, caplog):
    main_mod = main_rig
    main_mod._orc = _orc_with_cancelling_start()
    with caplog.at_level(logging.WARNING):
        await main_mod._start_discord_user_subscriber()  # must not raise
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("cancelled" in m for m in msgs), msgs


async def test_cancelled_start_reraises_during_shutdown(main_rig):
    main_mod = main_rig
    main_mod._orc = _orc_with_cancelling_start()
    main_mod._shutdown.set()
    with pytest.raises(asyncio.CancelledError):
        await main_mod._start_discord_user_subscriber()
