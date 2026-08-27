"""Tests for cerebral/trading/books.py (2026-08-26).

Pure and duck-typed, mirroring test_trading_discovery.py's own
conventions -- no real LLM, no real PDF, no real sandbox.
"""
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cerebral.trading.books import (
    Book, BookStore, chunk_text, extract_claims_from_chunk, extract_full_text, ingest_book,
    list_validated_strategies,
)
from cerebral.trading.discovery import DiscoveryWatchlist
from cerebral.trading.strategy_store import StrategySpec, StrategyStore


def _watchlist(tmp_path):
    return DiscoveryWatchlist(db_path=tmp_path / "watchlist.db")


def _store(tmp_path):
    return BookStore(db_path=tmp_path / "books.db")


class RecordingGauntlet:
    def __init__(self):
        self.calls = []

    async def __call__(self, idea, ticker):
        self.calls.append((idea, ticker))
        return {"ticker": ticker, "verdict": "VALIDATED"}


class FixedJudge:
    def __init__(self, accepted=True, reason="ok"):
        self._accepted, self._reason = accepted, reason

    async def __call__(self, idea):
        return self._accepted, self._reason


class FixedExtractor:
    """Fake claim extractor: returns a scripted list of claims per chunk,
    keyed by chunk order (calls[0] for the first chunk, etc.)."""
    def __init__(self, claims_per_chunk):
        self._claims_per_chunk = claims_per_chunk
        self.calls = []

    async def __call__(self, chunk):
        self.calls.append(chunk)
        idx = len(self.calls) - 1
        return self._claims_per_chunk[idx] if idx < len(self._claims_per_chunk) else []


# ── chunk_text ───────────────────────────────────────────────────────────

def test_chunk_text_packs_paragraphs_up_to_the_limit():
    text = "\n\n".join(["para one is short"] * 5)
    chunks = chunk_text(text, chunk_chars=40)
    assert len(chunks) > 1
    assert "".join(chunks).replace("\n\n", "") == "".join(["para one is short"] * 5)


def test_chunk_text_keeps_an_oversized_paragraph_intact():
    huge_para = "x" * 500
    chunks = chunk_text(huge_para, chunk_chars=100)
    assert chunks == [huge_para]


def test_chunk_text_handles_empty_input():
    assert chunk_text("") == []


# ── extract_full_text ────────────────────────────────────────────────────

def test_extract_full_text_reads_plain_text_file(tmp_path):
    p = tmp_path / "book.txt"
    # write_bytes, not write_text -- Windows' text-mode newline translation
    # would otherwise turn \n into \r\n and break the exact-match assertion.
    p.write_bytes("Chapter one.\n\nChapter two.".encode("utf-8"))
    assert extract_full_text(p) == "Chapter one.\n\nChapter two."


def test_extract_full_text_returns_empty_for_unsupported_extension(tmp_path):
    p = tmp_path / "book.xyz"
    p.write_bytes(b"whatever this format is, nothing here understands it")
    assert extract_full_text(p) == ""


def test_extract_full_text_falls_back_to_latin1_on_bad_utf8(tmp_path):
    p = tmp_path / "book.txt"
    p.write_bytes(b"\xe9caf\xe9")  # not valid utf-8
    text = extract_full_text(p)
    assert text  # decoded via latin-1 fallback, not raised


def test_extract_full_text_returns_empty_for_a_corrupt_epub(tmp_path):
    p = tmp_path / "book.epub"
    p.write_bytes(b"not really a zip file")
    assert extract_full_text(p) == ""


def _make_epub(path: Path, chapters: list) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for i, html in enumerate(chapters):
            zf.writestr(f"OEBPS/chapter{i}.xhtml", html)


def test_extract_full_text_reads_a_real_epub(tmp_path):
    p = tmp_path / "book.epub"
    _make_epub(p, [
        "<html><body><script>ignored();</script><p>Chapter one text.</p></body></html>",
        "<html><body><p>Chapter two text.</p></body></html>",
    ])

    text = extract_full_text(p)

    assert "Chapter one text." in text
    assert "Chapter two text." in text
    assert "ignored();" not in text  # script content stripped, not narrated


def test_extract_full_text_kindle_dispatches_to_the_extracted_epub(tmp_path):
    p = tmp_path / "book.mobi"
    p.write_bytes(b"fake mobi bytes")
    epub_path = tmp_path / "extracted" / "book.epub"
    epub_path.parent.mkdir()
    _make_epub(epub_path, ["<html><body><p>Kindle chapter text.</p></body></html>"])

    with patch("mobi.extract", return_value=(str(epub_path.parent), str(epub_path))):
        text = extract_full_text(p)

    assert "Kindle chapter text." in text


def test_extract_full_text_kindle_cleans_up_its_tempdir(tmp_path):
    p = tmp_path / "book.mobi"
    p.write_bytes(b"fake mobi bytes")
    extracted_dir = tmp_path / "extracted"
    extracted_dir.mkdir()
    html_path = extracted_dir / "book.html"
    html_path.write_text("<p>Some text.</p>", encoding="utf-8")

    with patch("mobi.extract", return_value=(str(extracted_dir), str(html_path))):
        extract_full_text(p)

    assert not extracted_dir.exists()  # shutil.rmtree ran in the finally block


def test_extract_full_text_kindle_failure_degrades_to_empty(tmp_path):
    p = tmp_path / "book.azw3"
    p.write_bytes(b"fake azw3 bytes")

    with patch("mobi.extract", side_effect=ValueError("could not extract")):
        assert extract_full_text(p) == ""


def test_extract_full_text_office_uses_libreoffice_headless_conversion(tmp_path):
    p = tmp_path / "book.docx"
    p.write_bytes(b"fake docx bytes")

    def fake_run(cmd, capture_output, timeout):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "book.txt").write_bytes("Docx chapter text.".encode("utf-8"))
        return type("R", (), {"returncode": 0, "stderr": b""})()

    with patch("plugins.documents.find_soffice", return_value=Path("C:/soffice.exe")), \
         patch("subprocess.run", side_effect=fake_run):
        text = extract_full_text(p)

    assert text == "Docx chapter text."


def test_extract_full_text_office_without_libreoffice_degrades_to_empty(tmp_path):
    p = tmp_path / "book.rtf"
    p.write_bytes(b"fake rtf bytes")

    with patch("plugins.documents.find_soffice", return_value=None):
        assert extract_full_text(p) == ""


# ── BookStore ────────────────────────────────────────────────────────────

def test_book_store_add_and_get_round_trips(tmp_path):
    store = _store(tmp_path)
    added = store.add("Market Wizards", "wizards.pdf", "/data/books/1/wizards.pdf")

    fetched = store.get(added.id)

    assert fetched is not None
    assert fetched.title == "Market Wizards"
    assert fetched.status == "queued"
    assert fetched.total_chunks == 0


def test_book_store_progress_updates_persist(tmp_path):
    store = _store(tmp_path)
    book = store.add("Reminiscences", "rem.pdf", "/x/rem.pdf")

    store.set_total_chunks(book.id, 10)
    store.update_progress(book.id, 3, 2)

    fetched = store.get(book.id)
    assert fetched.status == "processing"
    assert fetched.total_chunks == 10
    assert fetched.processed_chunks == 3
    assert fetched.strategies_found == 2


def test_book_store_set_done_and_set_error(tmp_path):
    store = _store(tmp_path)
    b1 = store.add("A", "a.pdf", "/a.pdf")
    b2 = store.add("B", "b.pdf", "/b.pdf")

    store.set_done(b1.id)
    store.set_error(b2.id, "unreadable PDF")

    assert store.get(b1.id).status == "done"
    err = store.get(b2.id)
    assert err.status == "error"
    assert err.error_message == "unreadable PDF"


def test_book_store_set_stopped(tmp_path):
    store = _store(tmp_path)
    book = store.add("Reminiscences", "rem.pdf", "/x/rem.pdf")
    store.set_total_chunks(book.id, 10)
    store.update_progress(book.id, 4, 1)

    store.set_stopped(book.id)

    fetched = store.get(book.id)
    assert fetched.status == "stopped"
    assert fetched.processed_chunks == 4  # progress frozen in place, not cleared
    assert fetched.strategies_found == 1


def test_book_store_reset_clears_progress_back_to_queued(tmp_path):
    store = _store(tmp_path)
    book = store.add("Reminiscences", "rem.pdf", "/x/rem.pdf")
    store.set_total_chunks(book.id, 10)
    store.update_progress(book.id, 4, 1)
    store.set_error(book.id, "some earlier failure")

    store.reset(book.id)

    fetched = store.get(book.id)
    assert fetched.status == "queued"
    assert fetched.total_chunks == 0
    assert fetched.processed_chunks == 0
    assert fetched.strategies_found == 0
    assert fetched.error_message == ""


def test_book_store_delete_removes_the_row(tmp_path):
    store = _store(tmp_path)
    keep = store.add("Keep Me", "keep.pdf", "/keep.pdf")
    gone = store.add("Delete Me", "gone.pdf", "/gone.pdf")

    store.delete(gone.id)

    assert store.get(gone.id) is None
    assert store.get(keep.id) is not None
    assert [b.title for b in store.list_all()] == ["Keep Me"]


def test_book_store_list_all_orders_newest_first(tmp_path):
    store = _store(tmp_path)
    store.add("First", "1.pdf", "/1.pdf")
    store.add("Second", "2.pdf", "/2.pdf")

    titles = [b.title for b in store.list_all()]

    assert titles == ["Second", "First"]


# ── ingest_book ──────────────────────────────────────────────────────────

async def test_ingest_book_dispatches_a_claim_that_names_a_ticker(tmp_path):
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    extractor = FixedExtractor([["AAPL tends to rally after strong earnings beats."]])

    result = await ingest_book(
        ["chunk one text"], "Some Book", wl, gauntlet, extractor,
    )

    assert result == {"chunks": 1, "claims_seen": 1, "dispatched": 1}
    assert gauntlet.calls[0][1] == "AAPL"


async def test_ingest_book_skips_chunks_with_no_claims():
    wl_calls = []

    class NullWatchlist:
        def prefilter_candidates(self, idea, limit=3, rank_fn=None):
            wl_calls.append(idea)
            return []

        def upsert(self, *a, **kw):
            pass

    extractor = FixedExtractor([[], [], []])
    gauntlet = RecordingGauntlet()

    result = await ingest_book(
        ["c1", "c2", "c3"], "Empty Book", NullWatchlist(), gauntlet, extractor,
    )

    assert result == {"chunks": 3, "claims_seen": 0, "dispatched": 0}
    assert gauntlet.calls == []


async def test_ingest_book_runs_pattern_general_claims_through_the_judge(tmp_path):
    wl = _watchlist(tmp_path)
    wl.upsert("MSFT")
    gauntlet = RecordingGauntlet()
    extractor = FixedExtractor([["Stocks that fall 3 days in a row tend to bounce."]])
    judge = FixedJudge(accepted=False, reason="too vague")

    result = await ingest_book(
        ["chunk"], "Vague Book", wl, gauntlet, extractor, judge_idea_fn=judge,
    )

    assert result["dispatched"] == 0
    assert gauntlet.calls == []


async def test_ingest_book_reports_progress_after_each_chunk():
    progress_calls = []
    extractor = FixedExtractor([["AAPL always goes up on Tuesdays."], []])
    gauntlet = RecordingGauntlet()

    class NullWatchlist:
        def prefilter_candidates(self, idea, limit=3, rank_fn=None):
            return []

        def upsert(self, *a, **kw):
            pass

    await ingest_book(
        ["chunk one", "chunk two"], "Progress Book", NullWatchlist(), gauntlet, extractor,
        on_progress=lambda done, total, dispatched: progress_calls.append((done, total, dispatched)),
    )

    assert progress_calls == [(1, 2, 1), (2, 2, 1)]


async def test_extract_claims_from_chunk_with_no_router_yields_nothing():
    """Unlike judge_idea's 'accept by default', there's no idea yet to
    fall back on here -- a missing router just means this chunk produces
    no claims, not a crash."""
    assert await extract_claims_from_chunk("some excerpt", router=None) == []


async def test_extract_claims_from_chunk_uses_its_own_books_task_type():
    """2026-08-26: routed on task_type='books', not 'coding' -- a real book
    is hundreds of background chunk passes nobody is waiting on, so it gets
    its own model mapping independent of day-to-day coding chat."""
    class RecordingRouter:
        def __init__(self):
            self.calls = []

        async def complete(self, prompt, task_type):
            self.calls.append(task_type)
            return "NONE"

    router = RecordingRouter()
    await extract_claims_from_chunk("some excerpt", router=router)

    assert router.calls == ["books"]


async def test_ingest_book_uses_rank_fn_and_candidate_limit(tmp_path):
    wl = _watchlist(tmp_path)
    for sym in ["AAPL", "MSFT", "TSLA"]:
        wl.upsert(sym)
    gauntlet = RecordingGauntlet()
    extractor = FixedExtractor([["Mean reversion after a losing streak."]])

    await ingest_book(
        ["chunk"], "Ranked Book", wl, gauntlet, extractor,
        rank_fn=lambda symbols: ["TSLA", "MSFT", "AAPL"], candidate_limit=1,
    )

    assert [c[1] for c in gauntlet.calls] == ["TSLA"]


# ── list_validated_strategies (2026-08-27) ──────────────────────────────
# "N strategies found" in the Books panel was actually every gauntlet
# dispatch attempt (pass or fail) -- this is the real validated/persisted
# count/list a book actually produced.

def _save_book_strategy(store, strategy_id, symbol, book_title, chapter, hypothesis="a claim"):
    store.save(
        StrategySpec(strategy_id=strategy_id, symbol=symbol, code="def strategy(data):\n    return [0]\n"),
        origin="discovered",
        provenance_json={"source": f"book: {book_title} ch {chapter}"},
        hypothesis=hypothesis,
    )


def test_list_validated_strategies_matches_on_book_provenance(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")
    _save_book_strategy(store, "s1", "AAPL", "Market Wizards", "chunk 3", hypothesis="claim one")
    _save_book_strategy(store, "s2", "MSFT", "A Different Book", "chunk 1", hypothesis="unrelated")

    results = list_validated_strategies("Market Wizards", store)

    assert len(results) == 1
    assert results[0]["symbol"] == "AAPL"
    assert results[0]["hypothesis"] == "claim one"
    assert results[0]["chapter"] == "chunk 3"


def test_list_validated_strategies_excludes_non_book_origins(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")
    store.save(
        StrategySpec(strategy_id="web1", symbol="TSLA", code="def strategy(data):\n    return [0]\n"),
        origin="discovered", provenance_json={"source": "https://example.com/idea"},
    )

    assert list_validated_strategies("Market Wizards", store) == []


def test_list_validated_strategies_returns_empty_for_a_book_with_no_hits(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")

    assert list_validated_strategies("Nonexistent Book", store) == []


def test_list_validated_strategies_only_matches_this_books_exact_title(tmp_path):
    """A book titled 'Market Wizards' must not also match strategies from
    'New Market Wizards' -- the match is on the full `book: <title> ch `
    prefix, not a loose substring."""
    store = StrategyStore(db_path=tmp_path / "specs.db")
    _save_book_strategy(store, "s1", "AAPL", "New Market Wizards", "chunk 1")

    assert list_validated_strategies("Market Wizards", store) == []
