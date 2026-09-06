import os
import tempfile

# Isolate all persistent state before any test module imports cerebral.main —
# main constructs its module-level stores at import time, and every store
# defaults its path from cerebral.paths.data_dir(). Without this, test runs
# write into the real per-user cerebral/data/openmind.db (profiles, turns,
# insight signals, credentials). setdefault so a caller can still point the
# suite at a specific dir deliberately.
os.environ.setdefault("OPENMIND_DATA_DIR", tempfile.mkdtemp(prefix="openmind-test-data-"))

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
def _cleanup_worker_heartbeat_task():
    """Ensure no dangling _worker_heartbeat_task survives test teardown.

    Some test fixtures construct a Cerebral app or trigger `_wire_session_worker`,
    which spawns the background heartbeat task. If the task isn't explicitly
    cancelled, Python's garbage collector destroys it on interpreter exit,
    producing a RuntimeWarning and non-zero exit codes on Windows. This autouse
    fixture safely cancels it if present.
    """
    yield
    try:
        import cerebral.main as _cm
        task = _cm._worker_heartbeat_task
        if task is not None and not task.done():
            task.cancel()
    except (ImportError, AttributeError):
        pass