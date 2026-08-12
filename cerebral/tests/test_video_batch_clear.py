"""Tests for video_batch_clear -- ADR-0017 S21 (clear the unwatched queue)."""
from __future__ import annotations

import asyncio
import json

import pytest

from cerebral.video.store import VideoStore
import plugins.video as video_mod


@pytest.fixture
def db(tmp_path):
    return VideoStore(db_path=tmp_path / "test.db")


def _plugin(db):
    p = video_mod.create()
    video_mod.set_store(db)
    return p


def _call(p, args=None):
    return asyncio.run(p.call_tool("video_batch_clear", args or {}))


def test_clears_only_enumerated(db):
    db.upsert("http://x/1", stage="enumerated")
    db.upsert("http://x/2", stage="enumerated")
    db.upsert("http://x/3", stage="verified")   # watched -> keep
    p = _plugin(db)
    data = json.loads(_call(p).content)
    assert data["cleared"] == 2
    assert db.get_by_url("http://x/3") is not None       # watched survives
    assert db.get_by_url("http://x/1") is None            # queue gone
    assert db.total_pending() == 0


def test_scoped_to_channel(db):
    db.upsert("http://a/1", channel="A", stage="enumerated")
    db.upsert("http://b/1", channel="B", stage="enumerated")
    p = _plugin(db)
    data = json.loads(_call(p, {"channel": "A"}).content)
    assert data["cleared"] == 1
    assert db.get_by_url("http://b/1") is not None        # other channel untouched


def test_clear_empty_queue_is_zero(db):
    db.upsert("http://x/1", stage="verified")
    p = _plugin(db)
    data = json.loads(_call(p).content)
    assert data["cleared"] == 0


def test_store_clear_pending_keeps_clusters(db):
    db.upsert("http://x/1", stage="enumerated")
    cid = db.get_or_create_cluster("some idea")
    assert db.clear_pending() == 1
    assert db.get_cluster_by_id(cid) is not None          # clusters untouched
