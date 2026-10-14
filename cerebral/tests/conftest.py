import os
import tempfile

# Isolate all persistent state before any test module imports cerebral.main —
# main constructs its module-level stores at import time, and every store
# defaults its path from cerebral.paths.data_dir(). Without this, test runs
# write into the real per-user cerebral/data/openmind.db (profiles, turns,
# insight signals, credentials). setdefault so a caller can still point the
# suite at a specific dir deliberately.
os.environ.setdefault("OPENMIND_DATA_DIR", tempfile.mkdtemp(prefix="openmind-test-data-"))

import asyncio
import contextlib
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires live services (Ollama, OpenClaw); run with -m integration",
    )


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="live services not available; run with -m integration")
    for item in items:
        if "integration" in item.keywords and not config.option.markexpr:
            item.add_marker(skip_integration)


@pytest.fixture(autouse=True)
async def _cancel_worker_heartbeat_task():
    """#1377/#1107/#1110: a test can leave cerebral.main._worker_heartbeat_task
    pending; its destruction at interpreter shutdown flips pytest's exit code.
    Cancel AND await it (async fixture shares the test's loop). Never imports
    cerebral.main -- only acts if a test already did."""
    yield
    main = sys.modules.get("cerebral.main")
    task = getattr(main, "_worker_heartbeat_task", None)
    if task is None:
        return
    main._worker_heartbeat_task = None
    if task.done():
        return
    task.cancel()
    if task.get_loop() is asyncio.get_running_loop():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
