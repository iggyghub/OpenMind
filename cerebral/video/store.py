"""Video store -- SQLite backing for the video-watching primitive (ADR-0017).

Schema lives in openmind.db as a `videos` table.  Every stage transition is
committed per-video so the batch runner is fully resumable (ADR-0017 decision 4).

Stages: enumerated -> downloaded -> transcribed -> escalated -> extracted -> verified

S2 #640 adds: ocr_text, visual_summary, escalated columns.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cerebral.paths import data_dir

_DEFAULT_DB = data_dir() / "openmind.db"

_DDL = """
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT    NOT NULL UNIQUE,
    channel       TEXT,
    title         TEXT,
    duration      REAL,
    transcript    TEXT,
    ocr_text      TEXT,
    visual_summary TEXT,
    escalated     INTEGER DEFAULT 0,
    stage         TEXT    NOT NULL DEFAULT 'enumerated',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
"""

# Migration: add S2 columns to tables created before this slice.
_MIGRATIONS = [
    "ALTER TABLE videos ADD COLUMN ocr_text TEXT",
    "ALTER TABLE videos ADD COLUMN visual_summary TEXT",
    "ALTER TABLE videos ADD COLUMN escalated INTEGER DEFAULT 0",
]


@dataclass
class Video:
    id: int
    url: str
    channel: Optional[str]
    title: Optional[str]
    duration: Optional[float]
    transcript: Optional[str]
    ocr_text: Optional[str]
    visual_summary: Optional[str]
    escalated: bool
    stage: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "channel": self.channel,
            "title": self.title,
            "duration": self.duration,
            "transcript": self.transcript,
            "ocr_text": self.ocr_text,
            "visual_summary": self.visual_summary,
            "escalated": self.escalated,
            "stage": self.stage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class VideoStore:
    """Thread-safe via check_same_thread=False (same posture as JobSearchStore)."""

    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_DDL)
        self._run_migrations()

    def _run_migrations(self) -> None:
        for sql in _MIGRATIONS:
            try:
                self._con.execute(sql)
                self._con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

    def _conn(self) -> sqlite3.Connection:
        return self._con

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert(
        self,
        url: str,
        *,
        channel: str | None = None,
        title: str | None = None,
        duration: float | None = None,
        transcript: str | None = None,
        ocr_text: str | None = None,
        visual_summary: str | None = None,
        escalated: bool | None = None,
        stage: str = "enumerated",
    ) -> int:
        """Insert or update a video row.  Returns the video id."""
        now = self._now()
        with self._conn() as con:
            cur = con.execute(
                """
                INSERT INTO videos
                    (url, channel, title, duration, transcript,
                     ocr_text, visual_summary, escalated,
                     stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    channel        = COALESCE(excluded.channel,        channel),
                    title          = COALESCE(excluded.title,          title),
                    duration       = COALESCE(excluded.duration,       duration),
                    transcript     = COALESCE(excluded.transcript,     transcript),
                    ocr_text       = COALESCE(excluded.ocr_text,       ocr_text),
                    visual_summary = COALESCE(excluded.visual_summary, visual_summary),
                    escalated      = COALESCE(excluded.escalated,      escalated),
                    stage          = excluded.stage,
                    updated_at     = excluded.updated_at
                """,
                (
                    url, channel, title, duration, transcript,
                    ocr_text, visual_summary,
                    int(escalated) if escalated is not None else None,
                    stage, now, now,
                ),
            )
            if cur.lastrowid and cur.lastrowid != 0:
                return cur.lastrowid
            row = con.execute("SELECT id FROM videos WHERE url = ?", (url,)).fetchone()
            return row["id"]

    def get_by_id(self, video_id: int) -> Video | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _row_to_video(row) if row else None

    def get_by_url(self, url: str) -> Video | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM videos WHERE url = ?", (url,)).fetchone()
        return _row_to_video(row) if row else None


def _row_to_video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        url=row["url"],
        channel=row["channel"],
        title=row["title"],
        duration=row["duration"],
        transcript=row["transcript"],
        ocr_text=row["ocr_text"] if "ocr_text" in row.keys() else None,
        visual_summary=row["visual_summary"] if "visual_summary" in row.keys() else None,
        escalated=bool(row["escalated"] or 0) if "escalated" in row.keys() else False,
        stage=row["stage"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
