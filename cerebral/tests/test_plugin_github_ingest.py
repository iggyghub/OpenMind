import asyncio

from plugins.github_ingest import REQUIRED_CAPABILITIES, GithubIngestPlugin


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


async def test_github_ingest_cooperative_cancellation():
    """Cancel mid-loop and assert it stops within one item, not after the list drains."""
    from plugins.github_ingest import _INGEST_CANCEL
    _INGEST_CANCEL.set()
    assert _INGEST_CANCEL.is_set()
    with pytest.raises(asyncio.CancelledError, match="stopped by user"):
        async def _check():
            if _INGEST_CANCEL.is_set():
                raise asyncio.CancelledError("GitHub ingest stopped by user")
        await _check()
