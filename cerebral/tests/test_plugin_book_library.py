"""Gate test for book_library plugin discovery (ADR-0034).

Mirrors cerebral/tests/test_plugin_trading_replay.py's pattern -- a real
guard-clause assertion, not a placeholder, so this file both satisfies the
orchestrator's REASON_NO_TEST_FILE gate and actually exercises something.
"""
import asyncio
import json

from plugins.book_library import BookLibraryPlugin, REQUIRED_CAPABILITIES, create
from cerebral.mcp.orchestrator import ToolResult


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert "fs_delete" in REQUIRED_CAPABILITIES


def test_unknown_tool_returns_error():
    plugin = create()
    result = asyncio.run(plugin.call_tool("nonexistent_tool", {}))
    assert result.is_error is True


class _RecordingGauntlet:
    """Fake TradingStrategiesPlugin -- records the args _run_book_ingestion's
    own run_gauntlet_fn closure builds, without running a real gauntlet."""
    def __init__(self):
        self.calls = []

    async def _run_gauntlet(self, args, **kwargs):
        self.calls.append(dict(args))
        return ToolResult(content=json.dumps({"verdict": "REJECTED"}), is_error=False)


# BOOK-TIMEFRAME S2 (#1267): run_gauntlet_fn (plugins/book_library.py's own
# closure inside _run_book_ingestion) must thread idea.interval into
# gauntlet_args when the claim's own text carries a detected timeframe, and
# must omit the key entirely otherwise -- mirrors discovery.py's existing
# "interval": interval line, which book ingestion never had before this
# slice. self_dev's own first attempt at this slice (PR #1270) tried to
# thread interval through cerebral.trading.books.ingest_book's kwargs into
# process_idea instead -- process_idea has no such parameter, so that
# would have raised TypeError on exactly the claims this slice exists to
# handle. Hand-fixed directly in the closure, which already has `idea` in
# scope and needs no new plumbing.
async def test_run_book_ingestion_threads_detected_interval_into_gauntlet_args(monkeypatch, tmp_path):
    plugin = BookLibraryPlugin(db_path=str(tmp_path / "openmind.db"))
    gauntlet = _RecordingGauntlet()
    plugin._gauntlet = gauntlet

    # Names AAPL explicitly so extract_ticker routes straight to
    # run_gauntlet_fn (process_idea's ticker-specific path), skipping the
    # judge/watchlist/candidate-limit machinery entirely -- same reasoning
    # test_trading_books.py's own ingest_book tests use a ticker-naming
    # claim, this test only needs run_gauntlet_fn's own interval handling.
    claim = "AAPL: exit any trade that remains in a loss after holding for 45 minutes from entry"

    async def fake_extract(chunk, router):
        return [claim]

    monkeypatch.setattr("plugins.book_library.extract_claims_from_chunk", fake_extract)

    book = plugin._book_store.add("The Trading Book", "book.pdf", "/data/books/1/book.pdf")

    await plugin._run_book_ingestion(book.id, ["chunk one"], "The Trading Book")

    assert len(gauntlet.calls) == 1
    assert gauntlet.calls[0]["interval"] == "30m"


async def test_run_book_ingestion_omits_interval_when_claim_has_no_timeframe(monkeypatch, tmp_path):
    plugin = BookLibraryPlugin(db_path=str(tmp_path / "openmind.db"))
    gauntlet = _RecordingGauntlet()
    plugin._gauntlet = gauntlet

    claim = "AAPL: buy shares trading below their liquidating value."

    async def fake_extract(chunk, router):
        return [claim]

    monkeypatch.setattr("plugins.book_library.extract_claims_from_chunk", fake_extract)

    book = plugin._book_store.add("Value Investing", "book.pdf", "/data/books/2/book.pdf")

    await plugin._run_book_ingestion(book.id, ["chunk one"], "Value Investing")

    assert len(gauntlet.calls) == 1
    assert "interval" not in gauntlet.calls[0]
