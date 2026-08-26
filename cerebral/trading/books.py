"""Book ingestion for discovery (2026-08-26).

Reads an ENTIRE uploaded book, chunks it, asks the LLM to pull out any
testable trading-strategy claims per chunk, and feeds each claim through
the exact same `process_idea` convergence point web-sourced ideas already
use (decision #33: symbol+hypothesis+code -> run_gauntlet stays the single
unchanged path). A book is just a different idea SOURCE -- everything
downstream (judging, candidate screening, dispatch, persistence) is
unchanged and reused as-is.

Pure and duck-typed, like discovery.py -- LLM calls (claim extraction) and
gauntlet dispatch come in as injected async callables so this stays
testable without a real router or real backtests.
"""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from cerebral.paths import data_dir
from cerebral.trading.discovery import (
    DiscoveryWatchlist, JudgeIdeaFn, RankCandidatesFn, RecordActivityFn,
    RecordAttemptFn, RunGauntletFn, process_idea,
)
from cerebral.trading_ideas import from_book_claim

logger = logging.getLogger(__name__)

_DB_PATH = data_dir() / "books.db"

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_STOPPED = "stopped"

# Extensions this module can pull text from. Real books arrive in whatever
# format the source sells them in, not just PDF -- ebook formats (epub,
# kindle mobi/azw3) and office formats (docx/doc/odt/rtf, via the same
# LibreOffice headless conversion the Documents campaign already wired up)
# are all in scope.
_PDF_SUFFIXES = frozenset({".pdf"})
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
_EPUB_SUFFIXES = frozenset({".epub"})
_KINDLE_SUFFIXES = frozenset({".mobi", ".azw", ".azw3"})
_OFFICE_SUFFIXES = frozenset({".docx", ".doc", ".odt", ".rtf"})

ClaimExtractorFn = Callable[[str], Awaitable[List[str]]]


@dataclass
class Book:
    id: int
    title: str
    filename: str
    stored_path: str
    status: str
    total_chunks: int
    processed_chunks: int
    strategies_found: int
    created_at: str
    error_message: str


class BookStore:
    """Same lightweight SQLite-file convention as DiscoveryWatchlist/
    StrategyStore -- one row per uploaded book, updated as ingestion
    (a long-running background task) makes progress."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path if db_path is not None else _DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                title            TEXT NOT NULL,
                filename         TEXT NOT NULL,
                stored_path      TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'queued',
                total_chunks     INTEGER NOT NULL DEFAULT 0,
                processed_chunks INTEGER NOT NULL DEFAULT 0,
                strategies_found INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                error_message    TEXT NOT NULL DEFAULT ''
            )
        """)
        self._con.commit()

    def add(self, title: str, filename: str, stored_path: str) -> Book:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._con.execute(
            "INSERT INTO books (title, filename, stored_path, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, filename, stored_path, STATUS_QUEUED, now),
        )
        self._con.commit()
        return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def set_total_chunks(self, book_id: int, total_chunks: int) -> None:
        self._con.execute(
            "UPDATE books SET total_chunks = ?, status = ? WHERE id = ?",
            (total_chunks, STATUS_PROCESSING, book_id),
        )
        self._con.commit()

    def update_progress(self, book_id: int, processed_chunks: int, strategies_found: int) -> None:
        self._con.execute(
            "UPDATE books SET processed_chunks = ?, strategies_found = ? WHERE id = ?",
            (processed_chunks, strategies_found, book_id),
        )
        self._con.commit()

    def set_done(self, book_id: int) -> None:
        self._con.execute(
            "UPDATE books SET status = ? WHERE id = ?", (STATUS_DONE, book_id),
        )
        self._con.commit()

    def set_error(self, book_id: int, message: str) -> None:
        self._con.execute(
            "UPDATE books SET status = ?, error_message = ? WHERE id = ?",
            (STATUS_ERROR, message, book_id),
        )
        self._con.commit()

    def set_stopped(self, book_id: int) -> None:
        self._con.execute(
            "UPDATE books SET status = ? WHERE id = ?", (STATUS_STOPPED, book_id),
        )
        self._con.commit()

    def reset(self, book_id: int) -> None:
        """Back to a fresh queued state, all progress/counters cleared --
        used by retry_book to redo a book's ingestion from scratch.
        total_chunks is set separately (set_total_chunks) once the file
        has been re-extracted and re-chunked."""
        self._con.execute(
            "UPDATE books SET status = ?, total_chunks = 0, processed_chunks = 0, "
            "strategies_found = 0, error_message = '' WHERE id = ?",
            (STATUS_QUEUED, book_id),
        )
        self._con.commit()

    def delete(self, book_id: int) -> None:
        self._con.execute("DELETE FROM books WHERE id = ?", (book_id,))
        self._con.commit()

    def get(self, book_id: int) -> Optional[Book]:
        row = self._con.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return _row_to_book(row) if row is not None else None

    def list_all(self) -> List[Book]:
        rows = self._con.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
        return [_row_to_book(r) for r in rows]

    def close(self) -> None:
        self._con.close()


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=row["id"], title=row["title"], filename=row["filename"],
        stored_path=row["stored_path"], status=row["status"],
        total_chunks=row["total_chunks"], processed_chunks=row["processed_chunks"],
        strategies_found=row["strategies_found"], created_at=row["created_at"],
        error_message=row["error_message"],
    )


class _TextOnlyHTMLParser(HTMLParser):
    """Strips tags/scripts/styles for a plain-text approximation of an
    HTML/XHTML page. Good enough for claim extraction -- exact reading
    order and formatting don't matter once the text is chunked and fed
    to an LLM anyway."""

    def __init__(self):
        super().__init__()
        self._skip = False
        self.pieces: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.pieces.append(data.strip())


def _html_to_text(html: str) -> str:
    parser = _TextOnlyHTMLParser()
    parser.feed(html)
    return "\n\n".join(parser.pieces)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
    except Exception:
        logger.exception("[books] Failed to open PDF %s", path)
        return ""
    pieces = []
    for page in reader.pages:
        try:
            pieces.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(p.strip() for p in pieces if p.strip())


def _extract_text_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _extract_epub_text(path: Path) -> str:
    """EPUB is a zip of XHTML content files. Reading-order comes from the
    OPF spine in a fully correct reader; sorting filenames gets a good
    enough approximation here (real epub tooling names chapter files
    sequentially) without adding an epub-parsing dependency."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = sorted(
                n for n in zf.namelist()
                if n.lower().endswith((".xhtml", ".html", ".htm"))
            )
            pieces = []
            for name in names:
                try:
                    raw = zf.read(name).decode("utf-8", errors="replace")
                except Exception:
                    continue
                pieces.append(_html_to_text(raw))
        return "\n\n".join(p.strip() for p in pieces if p.strip())
    except Exception:
        logger.exception("[books] Failed to open EPUB %s", path)
        return ""


def _extract_kindle_text(path: Path) -> str:
    """Kindle mobi/azw3 formats are a proprietary container; unpacks to an
    epub, raw html, or (rare, older format) a pdf depending on the book's
    internal version -- dispatch to whichever extractor matches."""
    try:
        import mobi
    except ImportError:
        logger.warning("[books] 'mobi' package not installed -- cannot extract %s", path)
        return ""
    tempdir = None
    try:
        tempdir, out_path = mobi.extract(str(path))
        out = Path(out_path)
        suffix = out.suffix.lower()
        if suffix == ".epub":
            return _extract_epub_text(out)
        if suffix == ".pdf":
            return _extract_pdf_text(out)
        if suffix in (".html", ".htm"):
            return _html_to_text(out.read_text(encoding="utf-8", errors="replace"))
        return ""
    except Exception:
        logger.exception("[books] Failed to extract Kindle book %s", path)
        return ""
    finally:
        if tempdir:
            shutil.rmtree(tempdir, ignore_errors=True)


def _extract_office_text(path: Path) -> str:
    """docx/doc/odt/rtf via the same headless LibreOffice conversion the
    Documents campaign already wired up (ADR-0011, plugins/documents.py)
    -- no new dependency, LibreOffice reads all four natively."""
    from plugins.documents import find_soffice
    soffice = find_soffice()
    if soffice is None:
        logger.warning("[books] LibreOffice not found -- cannot extract %s", path)
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(
                [str(soffice), "--headless", "--convert-to", "txt", "--outdir", tmp, str(path)],
                capture_output=True, timeout=120,
            )
        except Exception:
            logger.exception("[books] soffice conversion failed for %s", path)
            return ""
        if proc.returncode != 0:
            logger.warning(
                "[books] soffice exited %s for %s: %s",
                proc.returncode, path, (proc.stderr or b"").decode(errors="replace")[:200],
            )
            return ""
        out_path = Path(tmp) / f"{path.stem}.txt"
        return _extract_text_file(out_path) if out_path.exists() else ""


def extract_full_text(path: Path) -> str:
    """Full, uncapped text extraction -- deliberately separate from
    cerebral/db/attachments.py's extract_text(), which truncates at
    MAX_EXTRACTED_CHARS (32K) for folding into a single chat prompt. A
    real book needs its entire text chunked across many LLM passes, not
    one truncated blob."""
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        return _extract_pdf_text(path)
    if suffix in _TEXT_SUFFIXES:
        return _extract_text_file(path)
    if suffix in _EPUB_SUFFIXES:
        return _extract_epub_text(path)
    if suffix in _KINDLE_SUFFIXES:
        return _extract_kindle_text(path)
    if suffix in _OFFICE_SUFFIXES:
        return _extract_office_text(path)
    return ""


def chunk_text(text: str, chunk_chars: int = 6000) -> List[str]:
    """Splits on paragraph boundaries, packing paragraphs into ~chunk_chars
    chunks rather than cutting mid-sentence. A single paragraph longer than
    chunk_chars becomes its own (oversized) chunk rather than being cut --
    a claim's context matters more than a hard size cap here."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for para in paragraphs:
        if current and current_len + len(para) > chunk_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def extract_claims_from_chunk(chunk: str, router) -> List[str]:
    """Default claim extractor: one LLM pass per chunk. Routed on its own
    task_type='books' (2026-08-26) rather than reusing 'coding' -- a real
    book runs hundreds of these calls as a background job with no one
    waiting on it, so it's worth pointing at a slower/more careful model
    than day-to-day coding chat without touching that mapping. judge_idea/
    to_strategy stay on 'coding' -- they're the same shared convergence
    point (decision #33) a web-sourced idea also goes through, so
    book-vs-web provenance shouldn't change which model screens/writes the
    actual strategy code. Returns zero or more testable claims, one per
    line in the response."""
    prompt = (
        "You are extracting testable trading-strategy claims from a book "
        "excerpt. A TESTABLE claim names a specific, measurable market "
        "behavior (a price pattern, an indicator threshold, an event "
        "reaction) that could be encoded as `def strategy(data) -> "
        "signals`. Ignore narrative, biography, and general market "
        "commentary that makes no falsifiable prediction.\n\n"
        f"Excerpt:\n{chunk}\n\n"
        "List each distinct testable claim on its own line, verbatim "
        "enough to backtest. If there are none, respond with exactly: NONE."
    )
    if router is None:
        # No router configured -- unlike judge_idea's "accept by default"
        # (there's already an idea to fall back on), there's nothing
        # sensible to extract without a real LLM call, so this chunk
        # simply yields no claims rather than raising.
        return []
    try:
        raw = await router.complete(prompt, task_type="books")
    except Exception as exc:
        logger.warning("[books] claim extraction failed for a chunk (%s); skipping", exc)
        return []
    raw = (raw or "").strip()
    if not raw or raw.upper() == "NONE":
        return []
    return [line.strip("-* \t") for line in raw.splitlines() if line.strip() and line.strip().upper() != "NONE"]


async def ingest_book(
    chunks: List[str],
    title: str,
    watchlist: DiscoveryWatchlist,
    run_gauntlet_fn: RunGauntletFn,
    claim_extractor_fn: ClaimExtractorFn,
    judge_idea_fn: Optional[JudgeIdeaFn] = None,
    record_activity_fn: Optional[RecordActivityFn] = None,
    record_attempt_fn: Optional[RecordAttemptFn] = None,
    rank_fn: Optional[RankCandidatesFn] = None,
    candidate_limit: int = 3,
    on_progress: Optional[Callable[[int, int, int], None]] = None,
) -> dict:
    """One full book, chunk by chunk: extract claims -> process_idea for
    each claim (judge, screen, dispatch -- identical to a web-sourced
    idea). `on_progress(chunks_done, total_chunks, strategies_dispatched)`
    fires after every chunk so a caller can persist/broadcast progress
    without this function knowing about BookStore or IPC at all."""
    total = len(chunks)
    dispatched = 0
    claims_seen = 0
    for i, chunk in enumerate(chunks):
        claims = await claim_extractor_fn(chunk)
        for claim in claims:
            claims_seen += 1
            idea = from_book_claim(claim, title, f"chunk {i + 1}")
            results = await process_idea(
                idea, watchlist, run_gauntlet_fn,
                judge_idea_fn=judge_idea_fn, record_activity_fn=record_activity_fn,
                record_attempt_fn=record_attempt_fn, rank_fn=rank_fn,
                candidate_limit=candidate_limit,
            )
            dispatched += len(results)
        if on_progress is not None:
            on_progress(i + 1, total, dispatched)
    return {"chunks": total, "claims_seen": claims_seen, "dispatched": dispatched}
