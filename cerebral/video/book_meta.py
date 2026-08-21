"""Book metadata store -- ADR-0025 S2.

Keeps title/author/tier/edition/isbn alongside the S1 book identity.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cerebral.paths import data_dir

DB_PATH = data_dir() / "openmind.db"


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
