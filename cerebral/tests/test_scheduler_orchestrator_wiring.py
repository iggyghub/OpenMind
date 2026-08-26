"""Scheduler orchestrator-instance wiring guard (2026-08-26).

`_orc.discover_plugins()` auto-instantiates its OWN fresh `SchedulerPlugin()`
(scheduler.py's `create()`, no settings/router/watchlist wiring) and
registers it under `_orc._plugins["scheduler"]` -- separate from the bare
`_scheduler_plugin` global that `_scheduler_loop`/`_trading_broadcast`
actually drive. Every IPC/voice/chat tool call (start_discovery,
get_discovery_status, run_discovery, ...) went through that OTHER instance's
own settings cache, which only ever synced with the autonomous loop's copy
at the next full restart -- found live via a scheduler_heartbeat that kept
reporting stale/empty through get_discovery_status despite advancing on
disk. See .learnings/LEARNINGS.md for the full incident.

Fix: main.py re-registers the real _scheduler_plugin over the auto-
discovered one right after discover_plugins() (register() supports
replacing an already-registered name). This guards that replacement stays
in place.
"""
from __future__ import annotations

import cerebral.main as main


def test_registering_scheduler_plugin_replaces_the_dispatch_instance():
    """The exact call main.py makes after discover_plugins() -- verifies
    the orchestrator's real dispatch table (`_plugins`, used by
    `call_tool`) ends up pointing at the same object `_scheduler_loop`
    uses, not a separate auto-discovered instance."""
    main._orc.register(main._scheduler_plugin)

    assert main._orc._plugins["scheduler"] is main._scheduler_plugin


def test_scheduler_tool_dispatch_reaches_the_same_settings_store():
    """A settings mutation via the orchestrator's call_tool path must be
    visible to _scheduler_loop's own bare _settings read, and vice versa --
    the actual bug: two instances, two independently-cached SettingsStore
    copies of the same on-disk file."""
    main._orc.register(main._scheduler_plugin)

    dispatched = main._orc._plugins["scheduler"]
    assert dispatched._settings is main._settings
