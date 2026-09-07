import asyncio

import pytest

from plugins.github_ingest import (
    REQUIRED_CAPABILITIES,
    GithubIngestPlugin,
    _INGEST_CANCEL,
    _check_ingest_cancel,
)


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_github_ingest_requires_repo_url():
    # Real, deterministic guard-clause path -- exercises the actual code
    # without cloning a real repo over the network.
    plugin = GithubIngestPlugin()
    result = asyncio.run(plugin.call_tool("github_ingest", {}))
    assert result.is_error is True
    assert "repo_url" in result.content


@pytest.fixture(autouse=True)
def _reset_ingest_cancel():
    _INGEST_CANCEL.clear()
    yield
    _INGEST_CANCEL.clear()


async def test_check_ingest_cancel_raises_when_stopped():
    """The exact check _ingest_repo's loop calls once per doc (#1143):
    raises immediately once a stop is requested, so a caller iterating docs
    stops on the next document rather than draining the whole list."""
    _INGEST_CANCEL.set()
    with pytest.raises(asyncio.CancelledError, match="stopped by user"):
        await _check_ingest_cancel()


async def test_check_ingest_cancel_noop_when_not_stopped():
    _INGEST_CANCEL.clear()
    await _check_ingest_cancel()  # must not raise


def test_github_ingest_stop_and_reset_tools():
    plugin = GithubIngestPlugin()
    result = asyncio.run(plugin.call_tool("github_ingest_stop", {}))
    assert result.is_error is not True
    assert _INGEST_CANCEL.is_set()

    result = asyncio.run(plugin.call_tool("github_ingest_reset", {}))
    assert result.is_error is not True
    assert not _INGEST_CANCEL.is_set()
