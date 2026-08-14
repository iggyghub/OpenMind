"""Store tests for ADR-0018 S1 -- GitHub source items share the video store.

Covers: source_type column (default + explicit + preserved on re-upsert + the
migration backfill), list_clusters(source_type=...) filtering incl. mixed
clusters, and the github_repos side-table.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cerebral.video.store import VideoStore


def _store() -> VideoStore:
    return VideoStore(db_path=Path(":memory:"))


# ── source_type column ────────────────────────────────────────────────────────

def test_upsert_defaults_source_type_video():
    s = _store()
    s.upsert("https://yt/1", stage="transcribed")
    assert s.get_by_url("https://yt/1").source_type == "video"


def test_upsert_explicit_github_source_type():
    s = _store()
    s.upsert("https://gh/repo#README.md", stage="transcribed", source_type="github")
    assert s.get_by_url("https://gh/repo#README.md").source_type == "github"


def test_source_type_preserved_on_reupsert():
    # A github doc re-ingested (dedup path) without re-passing source_type stays github.
    s = _store()
    s.upsert("https://gh/repo#a.md", stage="transcribed", source_type="github")
    s.upsert("https://gh/repo#a.md", stage="extracted")  # no source_type
    v = s.get_by_url("https://gh/repo#a.md")
    assert v.source_type == "github"
    assert v.stage == "extracted"


def test_migration_backfills_existing_rows_to_video(tmp_path):
    # Simulate a pre-S1 DB: a videos table with no source_type column + a row.
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE, channel TEXT, title TEXT, duration REAL,
            transcript TEXT, stage TEXT NOT NULL DEFAULT 'enumerated',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO videos (url, stage, created_at, updated_at)
        VALUES ('https://yt/old', 'verified', '2026-01-01', '2026-01-01');
        """
    )
    con.commit()
    con.close()
    # Opening through VideoStore runs the migration; the old row backfills to 'video'.
    s = VideoStore(db_path=db)
    assert s.get_by_url("https://yt/old").source_type == "video"


# ── list_clusters(source_type=...) ────────────────────────────────────────────

def test_list_clusters_source_type_filter_and_mixed():
    s = _store()
    # A video-only cluster.
    v1 = s.upsert("https://yt/v", collection="c", stage="verified", source_type="video")
    cv = s.get_or_create_cluster("VideoOnly", "c")
    s.upsert_idea(v1, "vid idea", cv)
    # A github-only cluster.
    g1 = s.upsert("https://gh/r#a.md", collection="c", stage="verified", source_type="github")
    cg = s.get_or_create_cluster("GithubOnly", "c")
    s.upsert_idea(g1, "gh idea", cg)
    # A mixed cluster: one video idea + one github idea.
    v2 = s.upsert("https://yt/v2", collection="c", stage="verified", source_type="video")
    g2 = s.upsert("https://gh/r#b.md", collection="c", stage="verified", source_type="github")
    cm = s.get_or_create_cluster("Mixed", "c")
    s.upsert_idea(v2, "v2 idea", cm)
    s.upsert_idea(g2, "g2 idea", cm)

    vids = {c["label"] for c in s.list_clusters(source_type="video")}
    ghs = {c["label"] for c in s.list_clusters(source_type="github")}
    assert vids == {"VideoOnly", "Mixed"}      # mixed shows under video
    assert ghs == {"GithubOnly", "Mixed"}      # ...and under github
    assert {c["label"] for c in s.list_clusters()} == {"VideoOnly", "GithubOnly", "Mixed"}


# ── github_repos side-table ───────────────────────────────────────────────────

def test_github_repo_upsert_and_get():
    s = _store()
    s.upsert_github_repo("https://gh/r", head_sha="abc", description="a tool")
    r = s.get_github_repo("https://gh/r")
    assert r["head_sha"] == "abc"
    assert r["description"] == "a tool"


def test_github_repo_sha_refresh_keeps_description():
    # The ls-remote re-check refreshes head_sha only; description must survive.
    s = _store()
    s.upsert_github_repo("https://gh/r", head_sha="abc", description="a tool")
    s.upsert_github_repo("https://gh/r", head_sha="def")  # no description
    r = s.get_github_repo("https://gh/r")
    assert r["head_sha"] == "def"
    assert r["description"] == "a tool"


def test_github_repo_missing_returns_none():
    assert _store().get_github_repo("https://gh/none") is None
