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
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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

# Extensions this module can pull text from. EPUB deliberately not
# supported yet (no epub-parsing dependency installed) -- PDF/plain-text
# covers what's actually been asked for.
_PDF_SUFFIXES = frozenset({".pdf"})
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})

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


def extract_full_text(path: Path) -> str:
    """Full, uncapped text extraction -- deliberately separate from
    cerebral/db/attachments.py's extract_text(), which truncates at
    MAX_EXTRACTED_CHARS (32K) for folding into a single chat prompt. A
    real book needs its entire text chunked across many LLM passes, not
    one truncated blob."""
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
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
    if suffix in _TEXT_SUFFIXES:
        data = path.read_bytes()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")
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
    """Default claim extractor: one LLM pass per chunk, same free-routed
    model judge_idea/to_strategy already use (task_type='coding', decision
    #26 -- no new paid dependency). Returns zero or more testable claims,
    one per line in the response."""
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
        raw = await router.complete(prompt, task_type="coding")
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
