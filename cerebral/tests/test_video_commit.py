"""Tests for video_commit tool -- ADR-0017 S7 #645.

No network, no ChromaDB: commit_fn seam is fully stubbed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cerebral.video.store import VideoStore
import plugins.video as video_mod


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return VideoStore(db_path=tmp_path / "test.db")


@pytest.fixture(autouse=True)
def reset_commit_seam():
    video_mod.set_commit_fn(None)
    yield
    video_mod.set_commit_fn(None)


def _stub_commit(cluster_id, idea_text, cluster):
    return f"mem-{cluster_id}"


async def _async_stub_commit(cluster_id, idea_text, cluster):
    return f"async-mem-{cluster_id}"


# ── store: memory_id column ───────────────────────────────────────────────────

def test_get_cluster_by_id_returns_none_for_missing(db):
    assert db.get_cluster_by_id(999) is None


def test_get_cluster_by_id_returns_dict(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    c = db.get_cluster_by_id(cluster_id)
    assert c is not None
    assert c["label"] == "dropshipping"
    assert c["memory_id"] is None


def test_set_cluster_committed_stores_memory_id(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_committed(cluster_id, "mem-abc")
    c = db.get_cluster_by_id(cluster_id)
    assert c["memory_id"] == "mem-abc"


def test_list_clusters_includes_memory_id(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_committed(cluster_id, "mem-xyz")
    clusters = db.list_clusters()
    assert clusters[0]["memory_id"] == "mem-xyz"


def test_get_cluster_idea_text_returns_none_when_empty(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    assert db.get_cluster_idea_text(cluster_id) is None


def test_get_cluster_idea_text_returns_longest(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    vid1 = db.upsert("http://example.com/v1", stage="extracted")
    vid2 = db.upsert("http://example.com/v2", stage="extracted")
    db.upsert_idea(vid1, "short", cluster_id)
    db.upsert_idea(vid2, "a much longer idea text here", cluster_id)
    assert db.get_cluster_idea_text(cluster_id) == "a much longer idea text here"


# ── video_commit tool ─────────────────────────────────────────────────────────

def _make_plugin(db):
    p = video_mod.create()
    video_mod.set_store(db)
    return p


def test_commit_bad_cluster_id_type(db):
    p = _make_plugin(db)
    video_mod.set_commit_fn(_stub_commit)
    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": "not-an-int"}))
    assert result.is_error
    assert "integer" in result.content


def test_commit_unknown_cluster(db):
    p = _make_plugin(db)
    video_mod.set_commit_fn(_stub_commit)
    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": 999}))
    assert result.is_error
    assert "No cluster" in result.content


def test_commit_no_verdict(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    video_mod.set_commit_fn(_stub_commit)
    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert result.is_error
    assert "no verdict" in result.content.lower()


def test_commit_no_seam_wired(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "legit", 0.9, ["https://example.com"])
    # commit_fn is None (not wired)
    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert result.is_error
    assert "commit_fn not wired" in result.content


def test_commit_sync_seam_writes_memory(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "dubious", 0.7, ["https://example.com"])
    video_mod.set_commit_fn(_stub_commit)

    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert not result.is_error
    data = json.loads(result.content)
    assert data["committed"] is True
    assert data["memory_id"] == f"mem-{cluster_id}"
    assert data["verdict"] == "dubious"

    # Store updated
    c = db.get_cluster_by_id(cluster_id)
    assert c["memory_id"] == f"mem-{cluster_id}"


def test_commit_async_seam_writes_memory(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "legit", 0.95, ["https://example.com"])
    video_mod.set_commit_fn(_async_stub_commit)

    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert not result.is_error
    data = json.loads(result.content)
    assert data["memory_id"] == f"async-mem-{cluster_id}"


def test_commit_idempotent_no_duplicate(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "legit", 0.9, ["https://example.com"])

    calls = []

    def counting_commit(cid, idea_text, cluster):
        calls.append(cid)
        return f"mem-{cid}"

    video_mod.set_commit_fn(counting_commit)

    asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))

    assert not result.is_error
    data = json.loads(result.content)
    assert data["already_committed"] is True
    assert data["memory_id"] == f"mem-{cluster_id}"
    assert len(calls) == 1  # commit_fn called only once


def test_commit_uses_idea_text_from_store(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "scam", 0.95, ["https://example.com"])
    vid_id = db.upsert("http://example.com/v1", stage="extracted")
    db.upsert_idea(vid_id, "Sell stuff online via dropshipping", cluster_id)

    received = {}

    def capturing_commit(cid, idea_text, cluster):
        received["idea_text"] = idea_text
        return f"mem-{cid}"

    video_mod.set_commit_fn(capturing_commit)
    asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert received["idea_text"] == "Sell stuff online via dropshipping"


def test_commit_falls_back_to_label_when_no_ideas(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("print-on-demand")
    db.set_cluster_verdict(cluster_id, "legit", 0.8, ["https://example.com"])

    received = {}

    def capturing_commit(cid, idea_text, cluster):
        received["idea_text"] = idea_text
        return "mem-fallback"

    video_mod.set_commit_fn(capturing_commit)
    asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert received["idea_text"] == "print-on-demand"


def test_commit_seam_exception_returns_error(db):
    p = _make_plugin(db)
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "legit", 0.9, ["https://example.com"])

    def failing_commit(cid, idea_text, cluster):
        raise RuntimeError("memory store down")

    video_mod.set_commit_fn(failing_commit)
    result = asyncio.run(p.call_tool("video_commit", {"cluster_id": cluster_id}))
    assert result.is_error
    assert "memory store down" in result.content

    # memory_id must NOT be set on failure
    c = db.get_cluster_by_id(cluster_id)
    assert c["memory_id"] is None
