"""Tests for video_batch_retry -- reset transient failures back to the queue.

YouTube rate-blocks a yt-dlp burst mid-run, so most 'failed' rows are
recoverable, not broken. retry flips failed -> enumerated so a resume
re-attempts them (now authenticated via the browser cookies fix).
"""
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
    return asyncio.run(p.call_tool("video_batch_retry", args or {}))


# ── store.reset_failed ────────────────────────────────────────────────────────

def test_reset_failed_only_touches_failed(db):
    db.upsert("http://x/1", stage="failed")
    db.upsert("http://x/2", stage="failed")
    db.upsert("http://x/3", stage="verified")   # watched -> keep
    assert db.reset_failed() == 2
    assert db.get_by_url("http://x/1").stage == "enumerated"
    assert db.get_by_url("http://x/3").stage == "verified"
    assert db.total_pending() == 2               # the two failures re-queued


def test_reset_failed_scoped_by_collection(db):
    db.upsert("http://h/1", collection="harness improvement", stage="failed")
    db.upsert("http://m/1", collection="money-making idea", stage="failed")
    assert db.reset_failed(collection="harness improvement") == 1
    assert db.get_by_url("http://h/1").stage == "enumerated"
    assert db.get_by_url("http://m/1").stage == "failed"   # other collection untouched


# ── video_batch_retry tool ────────────────────────────────────────────────────

def test_retry_tool_resets_and_reports(db):
    db.upsert("http://x/1", collection="harness improvement", stage="failed")
    db.upsert("http://x/2", collection="harness improvement", stage="failed")
    p = _plugin(db)
    data = json.loads(_call(p, {"category": "harness improvement"}).content)
    assert data["reset"] == 2
    assert data["category"] == "harness improvement"
    assert db.total_pending() == 2


def test_retry_tool_no_failures_is_zero(db):
    db.upsert("http://x/1", stage="verified")
    p = _plugin(db)
    assert json.loads(_call(p).content)["reset"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
