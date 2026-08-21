"""Book metadata store -- ADR-0025 S2.

Keeps title/author/tier/edition/isbn alongside the S1 book identity.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

DB_PATH = data_dir() / "openmind.db"

_claim_extract_fn: Optional[Callable] = None


def set_claim_extract_fn(fn: "Callable | None") -> None:
    global _claim_extract_fn
    _claim_extract_fn = fn


def get_claim_extract_fn() -> Callable:
    return _claim_extract_fn or _default_claim_extract


async def _default_claim_extract(chapter_text: str, chapter_id: int, book_id: str, existing_concepts: list[str]) -> dict:
    # Ponytail: live-verify only -- in production, injected via main.py
    logger.warning("[book_meta] claim extraction seam stub -- wire set_claim_extract_fn via main.py")
    raise NotImplementedError("claim_extract_fn not wired")


class BookMetaStore:
    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY,
                profile_id INTEGER,
                title TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                edition TEXT NOT NULL DEFAULT '',
                publication_year INTEGER,
                isbn TEXT,
                source_tier INTEGER NOT NULL DEFAULT 3 CHECK (source_tier BETWEEN 1 AND 4),
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS book_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                chapter_id INTEGER NOT NULL,
                claim_text TEXT NOT NULL,
                claim_type TEXT NOT NULL CHECK (claim_type IN ('factual','empirical','theoretical','causal','predictive','methodological','normative','opinion','anecdotal','historical','definitional')),
                evidence_type TEXT NOT NULL CHECK (evidence_type IN ('empirical_data','statistical_analysis','academic_research','historical_example','case_study','logical_argument','mathematical_derivation','practitioner_experience','anecdote','unsupported_assertion')),
                confidence REAL,
                source_chunk_id TEXT NOT NULL,
                page INTEGER,
                concept_ids TEXT
            );
            CREATE TABLE IF NOT EXISTS book_assumptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER NOT NULL,
                assumption_text TEXT NOT NULL,
                FOREIGN KEY (claim_id) REFERENCES book_claims(id) ON DELETE CASCADE
            );
        """)
        self._con.commit()

    def upsert(self, book_id: str, *, profile_id: int | None, title: str,
               author: str = "", edition: str = "", publication_year: int | None = None,
               isbn: str = "", source_tier: int = 3) -> None:
        self._con.execute("""
            INSERT INTO books (id, profile_id, title, author, edition, publication_year, isbn, source_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, author=excluded.author, edition=excluded.edition,
                publication_year=excluded.publication_year, isbn=excluded.isbn,
                source_tier=excluded.source_tier, updated_at=CURRENT_TIMESTAMP
        """, (book_id, profile_id, title, author, edition, publication_year, isbn, source_tier))
        self._con.commit()

    def get(self, book_id: str) -> dict | None:
        row = self._con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            return None
        return dict(row)

    def list_for_profile(self, profile_id: int) -> list[dict]:
        rows = self._con.execute("""
            SELECT b.id, b.title, b.author, b.edition, b.publication_year, b.isbn,
                   b.source_tier, COUNT(v.id) as chapter_count,
                   SUM(CASE WHEN v.stage IN ('extracted', 'verified') THEN 1 ELSE 0 END) as clustered_count
            FROM books b
            LEFT JOIN videos v ON v.channel = b.id
            WHERE b.profile_id = ?
            GROUP BY b.id
            ORDER BY b.updated_at DESC
        """, (profile_id,)).fetchall()
        return [dict(r) for r in rows]
