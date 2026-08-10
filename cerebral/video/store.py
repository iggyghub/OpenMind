"""Video store — SQLite backing for the video-watching primitive (ADR-0017 S1 #639).

Schema lives in openmind.db as a `videos` table.  Every stage transition is
committed per-video so the batch runner is fully resumable (#639 AC3).

Stages: enumerated → downloaded → transcribed → escalated → extracted → verified
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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL UNIQUE,
    channel     TEXT,
    title       TEXT,
    duration    REAL,
    transcript  TEXT,
    stage       TEXT    NOT NULL DEFAULT 'enumerated',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""


@dataclass
class Video:
    id: int
    url: str
    channel: Optional[str]
    title: Optional[str]
    duration: Optional[float]
    transcript: Optional[str]
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
        stage: str = "enumerated",
    ) -> int:
        """Insert or update a video row.  Returns the video id."""
        now = self._now()
        with self._conn() as con:
            cur = con.execute(
                """
                INSERT INTO videos (url, channel, title, duration, transcript, stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    channel    = COALESCE(excluded.channel,    channel),
                    title      = COALESCE(excluded.title,      title),
                    duration   = COALESCE(excluded.duration,   duration),
                    transcript = COALESCE(excluded.transcript, transcript),
                    stage      = excluded.stage,
                    updated_at = excluded.updated_at
                """,
                (url, channel, title, duration, transcript, stage, now, now),
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
        stage=row["stage"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
