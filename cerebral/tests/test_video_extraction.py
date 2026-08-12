"""Tests for idea extraction + incremental clustering -- ADR-0017 S5 #642.

No network, no LLM calls: extract_fn seam is fully stubbed.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from cerebral.video import channel, extraction, pipeline
from cerebral.video.escalation import set_keyframe_fn, set_ocr_fn, set_vision_fn
from cerebral.video.store import VideoStore


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return VideoStore(db_path=tmp_path / "test.db")


@pytest.fixture(autouse=True)
def reset_seams():
    extraction.set_extract_fn(None)
    channel.set_enumerate_fn(None)
    pipeline.set_download_fn(None)
    pipeline.set_transcribe_fn(None)
    set_keyframe_fn(None)
    set_ocr_fn(None)
    set_vision_fn(None)
    channel._state.task = None
    channel._state.stop_flag = False
    channel._state.channel_url = None
    channel._state.timings = []
    yield
    extraction.set_extract_fn(None)
    channel._state.task = None
    channel._state.stop_flag = False
    channel._state.channel_url = None
    channel._state.timings = []


def _sync_stub(transcript, ocr_text, visual_summary, labels, category=""):
    return {"idea": "Sell stuff online", "cluster_label": "dropshipping"}


async def _async_stub(transcript, ocr_text, visual_summary, labels, category=""):
    return {"idea": "Sell stuff online", "cluster_label": "dropshipping"}


# ── extract_idea: sync and async seam ────────────────────────────────────────

def test_extract_idea_sync_seam():
    extraction.set_extract_fn(_sync_stub)
    result = asyncio.run(extraction.extract_idea("transcript", "", "", []))
    assert result["idea"] == "Sell stuff online"
    assert result["cluster_label"] == "dropshipping"


def test_extract_idea_async_seam():
    extraction.set_extract_fn(_async_stub)
    result = asyncio.run(extraction.extract_idea("transcript", "", "", []))
    assert result == {
        "idea": "Sell stuff online",
        "cluster_label": "dropshipping",
        "people_required": 1,
    }


def test_extract_idea_people_required_defaults_to_one_when_missing():
    # S8 #653: a missing people_required must not fail extraction; default solo.
    extraction.set_extract_fn(_async_stub)
    result = asyncio.run(extraction.extract_idea("t", "", "", []))
    assert result["people_required"] == 1


def test_extract_idea_people_required_honored_and_coerced():
    # S8 #653: a provided count is carried through; junk/<1 coerces to 1.
    async def _two(transcript, ocr, vis, labels, category=""):
        return {"idea": "Referral bonus with a friend", "cluster_label": "referral", "people_required": "2"}

    async def _junk(transcript, ocr, vis, labels, category=""):
        return {"idea": "x", "cluster_label": "y", "people_required": "lots"}

    extraction.set_extract_fn(_two)
    assert asyncio.run(extraction.extract_idea("t", "", "", []))["people_required"] == 2
    extraction.set_extract_fn(_junk)
    assert asyncio.run(extraction.extract_idea("t", "", "", []))["people_required"] == 1


# ── validate: retry on bad output ────────────────────────────────────────────

def test_extract_idea_retries_on_bad_output():
    calls = []

    def flaky(transcript, ocr_text, visual_summary, labels, category=""):
        calls.append(len(calls))
        if len(calls) < 3:
            return {"idea": "", "cluster_label": "ok"}  # empty idea -> invalid
        return {"idea": "Valid idea", "cluster_label": "valid-cluster"}

    extraction.set_extract_fn(flaky)
    result = asyncio.run(extraction.extract_idea("t", "", "", [], max_retries=3))
    assert result["idea"] == "Valid idea"
    assert len(calls) == 3


def test_extract_idea_raises_after_all_retries():
    def always_bad(transcript, ocr_text, visual_summary, labels, category=""):
        return {"idea": "", "cluster_label": "x"}

    extraction.set_extract_fn(always_bad)
    with pytest.raises(Exception):
        asyncio.run(extraction.extract_idea("t", "", "", [], max_retries=2))


def test_extract_idea_raises_on_non_dict():
    extraction.set_extract_fn(lambda *a: "not a dict")
    with pytest.raises(Exception):
        asyncio.run(extraction.extract_idea("t", "", "", [], max_retries=1))


# ── store: cluster + idea tables ─────────────────────────────────────────────

def test_get_cluster_labels_empty(db):
    assert db.get_cluster_labels() == []


def test_get_or_create_cluster_new(db):
    cid = db.get_or_create_cluster("dropshipping")
    assert isinstance(cid, int)
    assert db.get_cluster_labels() == ["dropshipping"]


def test_get_or_create_cluster_increments_member_count(db):
    db.get_or_create_cluster("dropshipping")
    db.get_or_create_cluster("dropshipping")
    from cerebral.video.store import _DEFAULT_DB
    import sqlite3
    # Check via the store's own connection
    con = db._con
    row = con.execute("SELECT member_count FROM video_clusters WHERE label='dropshipping'").fetchone()
    assert row["member_count"] == 2


def test_get_or_create_cluster_different_labels(db):
    c1 = db.get_or_create_cluster("dropshipping")
    c2 = db.get_or_create_cluster("affiliate marketing")
    assert c1 != c2
    assert set(db.get_cluster_labels()) == {"dropshipping", "affiliate marketing"}


def test_upsert_idea_and_get(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    vid = db.get_by_url("http://example.com/v1")
    cid = db.get_or_create_cluster("dropshipping")
    db.upsert_idea(vid.id, "Sell trending products via dropshipping", cid)

    idea = db.get_idea_for_video(vid.id)
    assert idea is not None
    assert idea["idea_text"] == "Sell trending products via dropshipping"
    assert idea["cluster_label"] == "dropshipping"
    assert idea["member_count"] == 1


def test_upsert_idea_replaces_existing(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    vid = db.get_by_url("http://example.com/v1")
    c1 = db.get_or_create_cluster("dropshipping")
    c2 = db.get_or_create_cluster("affiliate")
    db.upsert_idea(vid.id, "First idea", c1)
    db.upsert_idea(vid.id, "Updated idea", c2)

    idea = db.get_idea_for_video(vid.id)
    assert idea["idea_text"] == "Updated idea"
    assert idea["cluster_label"] == "affiliate"


def test_get_idea_for_video_none_when_missing(db):
    assert db.get_idea_for_video(9999) is None


# ── cluster label passing ─────────────────────────────────────────────────────

def test_existing_labels_passed_to_extract_fn(db):
    received_labels = []

    def capturing_stub(transcript, ocr_text, visual_summary, labels, category=""):
        received_labels.extend(labels)
        return {"idea": "Buy low sell high", "cluster_label": "trading"}

    db.get_or_create_cluster("dropshipping")
    db.get_or_create_cluster("affiliate")
    extraction.set_extract_fn(capturing_stub)

    asyncio.run(
        extraction.extract_idea("transcript", "", "", db.get_cluster_labels())
    )
    assert "dropshipping" in received_labels
    assert "affiliate" in received_labels


# ── batch integration: extraction wired into channel runner ───────────────────

def _stub_download(url, out_dir):
    audio = Path(out_dir) / "audio.mp3"
    audio.write_bytes(b"fake")
    return {"audio_path": audio, "title": "T", "duration": 10.0}


def test_batch_processes_to_extracted_stage(db):
    """Full happy-path: batch + extraction seam -> stage='extracted' + idea row."""
    call_count = [0]

    def extract_stub(transcript, ocr_text, visual_summary, labels, category=""):
        call_count[0] += 1
        return {"idea": f"Idea for video {call_count[0]}", "cluster_label": "testing"}

    channel.set_enumerate_fn(
        lambda url: [
            {"url": "http://example.com/v1", "title": "V1"},
            {"url": "http://example.com/v2", "title": "V2"},
        ]
    )
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))
    extraction.set_extract_fn(extract_stub)

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())

    v1 = db.get_by_url("http://example.com/v1")
    v2 = db.get_by_url("http://example.com/v2")
    assert v1.stage == "extracted"
    assert v2.stage == "extracted"

    idea1 = db.get_idea_for_video(v1.id)
    idea2 = db.get_idea_for_video(v2.id)
    assert idea1 is not None
    assert idea2 is not None
    # Both in same cluster -> member_count = 2
    assert idea1["cluster_label"] == "testing"
    assert idea2["member_count"] == 2


def test_batch_continues_if_extraction_fails(db):
    """Extraction failure leaves stage at transcribed but doesn't abort batch."""
    calls = [0]

    def failing_extract(transcript, ocr_text, visual_summary, labels, category=""):
        calls[0] += 1
        raise ValueError("LLM unavailable")

    channel.set_enumerate_fn(
        lambda url: [{"url": "http://example.com/v1", "title": "V1"}]
    )
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))
    extraction.set_extract_fn(failing_extract)

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())

    v1 = db.get_by_url("http://example.com/v1")
    # stage stays at transcribed, batch didn't crash
    assert v1.stage == "transcribed"
    assert db.get_idea_for_video(v1.id) is None
