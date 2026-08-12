"""Tests for video_query tool -- ADR-0017 S19 (filter/sort clusters + drill-in).

Pure store reads, no seams, no network.
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


def _make_plugin(db):
    p = video_mod.create()
    video_mod.set_store(db)
    return p


def _call(p, args):
    return asyncio.run(p.call_tool("video_query", args))


def _seed(db):
    """Three clusters: solo-legit-high, team-legit-low, solo-dubious."""
    a = db.get_or_create_cluster("Government Grants", people_required=1)
    db.set_cluster_verdict(a, "legit", 0.95, ["https://grants.gov"])
    v = db.upsert("http://x/a1", stage="extracted")
    db.upsert_idea(v, "Apply for federal grants", a)

    b = db.get_or_create_cluster("Community Building", people_required=2)
    db.set_cluster_verdict(b, "legit", 0.80, [])

    c = db.get_or_create_cluster("Get Rich Quick", people_required=1)
    db.set_cluster_verdict(c, "dubious", 0.60, [])
    return a, b, c


def test_list_all(db):
    _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {}).content)
    assert data["count"] == 3


def test_filter_verdict(db):
    _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"verdict": "legit"}).content)
    assert data["count"] == 2
    assert all(c["verdict"] == "legit" for c in data["clusters"])


def test_filter_solo_legit(db):
    """The user's motivating query: legit + doable solo."""
    a, _, _ = _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"verdict": "legit", "max_people": 1}).content)
    assert data["count"] == 1
    assert data["clusters"][0]["id"] == a


def test_min_confidence(db):
    _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"min_confidence": 0.9}).content)
    assert data["count"] == 1
    assert data["clusters"][0]["confidence"] == 0.95


def test_representative_video_attached(db):
    a, _, _ = _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"verdict": "legit", "max_people": 1}).content)
    rep = data["clusters"][0]["representative"]
    assert rep is not None and rep["url"] == "http://x/a1"


def test_representative_none_when_no_videos(db):
    _seed(db)  # cluster b has a verdict but no idea rows
    p = _make_plugin(db)
    data = json.loads(_call(p, {"verdict": "legit", "min_members": 0}).content)
    b = next(c for c in data["clusters"] if c["label"] == "Community Building")
    assert b["representative"] is None


def test_sort_confidence(db):
    _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"sort": "confidence"}).content)
    confs = [c["confidence"] for c in data["clusters"]]
    assert confs == sorted(confs, reverse=True)


def test_limit(db):
    _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"limit": 1}).content)
    assert data["count"] == 1


def test_uncommitted_filter(db):
    a, b, c = _seed(db)
    db.set_cluster_committed(a, "mem-1")
    p = _make_plugin(db)
    data = json.loads(_call(p, {"uncommitted": True}).content)
    assert all(not cl.get("memory_id") for cl in data["clusters"])
    assert a not in [cl["id"] for cl in data["clusters"]]


def test_drill_in(db):
    a, _, _ = _seed(db)
    p = _make_plugin(db)
    data = json.loads(_call(p, {"cluster_id": a}).content)
    assert data["id"] == a
    assert data["label"] == "Government Grants"
    assert len(data["videos"]) == 1
    assert data["videos"][0]["url"] == "http://x/a1"


def test_drill_in_unknown(db):
    _make_plugin(db)
    p = _make_plugin(db)
    result = _call(p, {"cluster_id": 999})
    assert result.is_error
    assert "No cluster" in result.content


def test_drill_in_bad_id(db):
    p = _make_plugin(db)
    result = _call(p, {"cluster_id": "nope"})
    assert result.is_error
    assert "integer" in result.content
