"""Tests for collection-scoped clusters + verify toggle -- S22 (ADR-0017).

Covers the money-idea de-hardcoding: a batch's category becomes the cluster
`collection`, scopes clustering, steers the extraction/verdict prompts, and the
verify=off path writes a "skipped" sentinel so a cluster is committable without
a fact-check. No network / no ChromaDB -- store + prompt builders are pure.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from cerebral.video.store import VideoStore
from cerebral.video.verdict import build_verdict_prompt


@pytest.fixture
def db(tmp_path):
    return VideoStore(db_path=tmp_path / "test.db")


# ── store: collection scoping ─────────────────────────────────────────────────

def test_same_label_two_collections_stays_separate(db):
    money = db.get_or_create_cluster("Agent Loops", collection="money-making idea")
    harness = db.get_or_create_cluster("Agent Loops", collection="harness improvement")
    assert money != harness  # same label, distinct clusters


def test_get_cluster_labels_scoped_by_collection(db):
    db.get_or_create_cluster("Grant Curation", collection="money-making idea")
    db.get_or_create_cluster("Context Engineering", collection="harness improvement")
    assert db.get_cluster_labels("harness improvement") == ["Context Engineering"]
    assert db.get_cluster_labels("money-making idea") == ["Grant Curation"]
    # unscoped returns both
    assert set(db.get_cluster_labels()) == {"Grant Curation", "Context Engineering"}


def test_list_clusters_filtered_by_collection(db):
    db.get_or_create_cluster("A", collection="money-making idea")
    db.get_or_create_cluster("B", collection="harness improvement")
    only = db.list_clusters(collection="harness improvement")
    assert [c["label"] for c in only] == ["B"]
    assert only[0]["collection"] == "harness improvement"


def test_get_or_create_cluster_reuses_within_collection(db):
    a = db.get_or_create_cluster("Same", collection="harness improvement")
    b = db.get_or_create_cluster("Same", collection="harness improvement")
    assert a == b
    assert db.get_cluster_by_id(a)["member_count"] == 2


def test_videos_carry_collection(db):
    db.enumerate_video("http://x/v1", channel="ch", collection="harness improvement")
    v = db.get_by_url("http://x/v1")
    assert v.collection == "harness improvement"


# ── verify toggle: "skipped" sentinel is committable ──────────────────────────

def test_skipped_verdict_is_set_and_truthy(db):
    cid = db.get_or_create_cluster("Tip", collection="harness improvement")
    db.set_cluster_verdict(cid, "skipped", None, [])
    c = db.get_cluster_by_id(cid)
    assert c["verdict"] == "skipped"          # truthy -> commit gate passes
    assert c["confidence"] is None            # never fact-checked


# ── verdict prompt: category swaps framing ────────────────────────────────────

def test_verdict_prompt_money_is_fraud_check():
    p = build_verdict_prompt("X", "Y", category="money-making idea")
    assert "financial-idea fact-checker" in p


def test_verdict_prompt_nonmoney_is_soundness_check():
    p = build_verdict_prompt("X", "Y", category="harness improvement")
    assert "financial-idea fact-checker" not in p
    assert "harness improvement" in p


# ── migration: pre-collection DB backfills to money ───────────────────────────

def test_migration_backfills_legacy_db(tmp_path):
    """An old DB (UNIQUE(label), no collection) upgrades: clusters -> money, ids kept."""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,
            channel TEXT, title TEXT, duration REAL, transcript TEXT,
            stage TEXT NOT NULL DEFAULT 'enumerated',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE video_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL UNIQUE,
            member_count INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO videos (url, channel, stage, created_at, updated_at)
            VALUES ('http://old/v1', 'lesko', 'verified', 't', 't');
        INSERT INTO video_clusters (id, label, member_count) VALUES (7, 'Grant Curation', 3);
        """
    )
    con.commit()
    con.close()

    store = VideoStore(db_path=path)  # triggers _migrate_collections
    clusters = store.list_clusters()
    assert clusters[0]["id"] == 7                              # id preserved
    assert clusters[0]["collection"] == "money-making idea"   # backfilled
    assert store.get_by_url("http://old/v1").collection == "money-making idea"
    # scoping constraint is now (collection, label): a harness "Grant Curation" coexists
    other = store.get_or_create_cluster("Grant Curation", collection="harness improvement")
    assert other != 7


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
