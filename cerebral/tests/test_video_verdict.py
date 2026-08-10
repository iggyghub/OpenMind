"""Tests for validity verdict per cluster -- ADR-0017 S6 #644.

No network, no API key: verify_fn seam is fully stubbed.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from cerebral.video import channel, extraction, pipeline, verdict
from cerebral.video.escalation import set_keyframe_fn, set_ocr_fn, set_vision_fn
from cerebral.video.store import VideoStore


# ── S9 #655: build_verdict_prompt (RAG grounding + de-bias) ───────────────────

def test_build_verdict_prompt_grounds_on_search_results():
    p = verdict.build_verdict_prompt("gov grants", "Apply for a federal grant", "RESULT: grants.gov says X")
    assert "RESULT: grants.gov says X" in p
    assert "Cite the relevant result URLs" in p
    assert "unethical" in p  # de-bias clause present


def test_build_verdict_prompt_falls_back_to_knowledge_when_no_results():
    p = verdict.build_verdict_prompt("gov grants", "Apply for a federal grant", "")
    assert "No web search results are available" in p
    assert "inventing URLs" in p
    assert "unethical" in p  # de-bias clause still present


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return VideoStore(db_path=tmp_path / "test.db")


@pytest.fixture(autouse=True)
def reset_seams():
    verdict.set_verify_fn(None)
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
    verdict.set_verify_fn(None)
    extraction.set_extract_fn(None)
    channel._state.task = None
    channel._state.stop_flag = False
    channel._state.channel_url = None
    channel._state.timings = []


def _stub_verdict(cluster_label, idea_text):
    return {"verdict": "legit", "confidence": 0.85, "evidence": ["https://example.com/source1"]}


def _stub_extract(transcript, ocr_text, visual_summary, labels):
    return {"idea": "Sell trending products online", "cluster_label": "dropshipping"}


# ── verify_cluster: sync and async seam ──────────────────────────────────────

def test_verify_cluster_sync_seam():
    verdict.set_verify_fn(_stub_verdict)
    result = asyncio.run(verdict.verify_cluster("dropshipping", "Sell stuff"))
    assert result["verdict"] == "legit"
    assert result["confidence"] == 0.85
    assert result["evidence"] == ["https://example.com/source1"]


async def _async_stub_verdict(cluster_label, idea_text):
    return {"verdict": "dubious", "confidence": 0.4, "evidence": ["https://example.com/s"]}


def test_verify_cluster_async_seam():
    verdict.set_verify_fn(_async_stub_verdict)
    result = asyncio.run(verdict.verify_cluster("dropshipping", "Sell stuff"))
    assert result["verdict"] == "dubious"
    assert result["confidence"] == 0.4


# ── validate: retry on bad output ────────────────────────────────────────────

def test_verify_cluster_retries_on_invalid_verdict():
    calls = []

    def flaky(cluster_label, idea_text):
        calls.append(len(calls))
        if len(calls) < 3:
            return {"verdict": "INVALID", "confidence": 0.5, "evidence": ["x"]}
        return {"verdict": "scam", "confidence": 0.9, "evidence": ["https://example.com"]}

    verdict.set_verify_fn(flaky)
    result = asyncio.run(verdict.verify_cluster("dropshipping", "Sell stuff", max_retries=3))
    assert result["verdict"] == "scam"
    assert len(calls) == 3


def test_verify_cluster_raises_after_all_retries():
    def always_bad(cluster_label, idea_text):
        return {"verdict": "legit", "confidence": 2.0, "evidence": ["x"]}  # confidence out of range

    verdict.set_verify_fn(always_bad)
    with pytest.raises(Exception):
        asyncio.run(verdict.verify_cluster("x", "y", max_retries=2))


def test_verify_cluster_raises_on_non_dict():
    verdict.set_verify_fn(lambda *a: "not a dict")
    with pytest.raises(Exception):
        asyncio.run(verdict.verify_cluster("x", "y", max_retries=1))


def test_verify_cluster_raises_on_empty_evidence():
    verdict.set_verify_fn(
        lambda *a: {"verdict": "legit", "confidence": 0.8, "evidence": []}
    )
    with pytest.raises(Exception):
        asyncio.run(verdict.verify_cluster("x", "y", max_retries=1))


def test_verify_cluster_raises_on_too_many_evidence():
    verdict.set_verify_fn(
        lambda *a: {"verdict": "legit", "confidence": 0.8, "evidence": ["a", "b", "c", "d"]}
    )
    with pytest.raises(Exception):
        asyncio.run(verdict.verify_cluster("x", "y", max_retries=1))


# ── store: verdict columns ────────────────────────────────────────────────────

def test_get_cluster_verdict_none_before_set(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    assert db.get_cluster_verdict(cluster_id) is None


def test_set_and_get_cluster_verdict(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "legit", 0.9, ["https://example.com"])
    v = db.get_cluster_verdict(cluster_id)
    assert v is not None
    assert v["verdict"] == "legit"
    assert v["confidence"] == 0.9
    assert v["evidence"] == ["https://example.com"]


def test_list_clusters_includes_verdict(db):
    cluster_id = db.get_or_create_cluster("dropshipping")
    db.set_cluster_verdict(cluster_id, "scam", 0.95, ["https://a.com", "https://b.com"])
    clusters = db.list_clusters()
    assert len(clusters) == 1
    c = clusters[0]
    assert c["verdict"] == "scam"
    assert c["confidence"] == 0.95
    assert c["evidence"] == ["https://a.com", "https://b.com"]


def test_list_clusters_verdict_none_when_unverified(db):
    db.get_or_create_cluster("affiliate")
    clusters = db.list_clusters()
    assert clusters[0]["verdict"] is None
    assert clusters[0]["confidence"] is None
    assert clusters[0]["evidence"] is None


# ── batch integration: first video verifies, second inherits ──────────────────

def _stub_download(url, out_dir):
    audio = Path(out_dir) / "audio.mp3"
    audio.write_bytes(b"fake")
    return {"audio_path": audio, "title": "T", "duration": 10.0}


def test_batch_first_video_verifies_second_inherits(db):
    """First video in a cluster calls verify_fn once; second inherits."""
    verify_calls = [0]

    def counting_verify(cluster_label, idea_text):
        verify_calls[0] += 1
        return {"verdict": "legit", "confidence": 0.8, "evidence": ["https://example.com"]}

    channel.set_enumerate_fn(
        lambda url: [
            {"url": "http://example.com/v1", "title": "V1"},
            {"url": "http://example.com/v2", "title": "V2"},
        ]
    )
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))
    extraction.set_extract_fn(_stub_extract)  # both videos -> same cluster
    verdict.set_verify_fn(counting_verify)

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())

    # verify_fn called exactly once (first video); second inherited
    assert verify_calls[0] == 1

    v1 = db.get_by_url("http://example.com/v1")
    v2 = db.get_by_url("http://example.com/v2")
    assert v1.stage == "verified"
    assert v2.stage == "verified"

    clusters = db.list_clusters()
    assert len(clusters) == 1
    assert clusters[0]["verdict"] == "legit"
    assert clusters[0]["confidence"] == 0.8


def test_batch_verdict_failure_leaves_stage_at_extracted(db):
    """Verdict failure leaves stage at 'extracted' but doesn't abort batch."""
    channel.set_enumerate_fn(
        lambda url: [{"url": "http://example.com/v1", "title": "V1"}]
    )
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))
    extraction.set_extract_fn(_stub_extract)
    verdict.set_verify_fn(lambda *a: (_ for _ in ()).throw(ValueError("model down")))

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())

    v1 = db.get_by_url("http://example.com/v1")
    assert v1.stage == "extracted"  # not crashed, just not verified


def test_batch_different_clusters_each_verified_once(db):
    """Two distinct clusters each trigger their own verify call."""
    verify_calls = []

    def tracking_verify(cluster_label, idea_text):
        verify_calls.append(cluster_label)
        return {"verdict": "dubious", "confidence": 0.5, "evidence": ["https://example.com"]}

    call_count = [0]

    def two_cluster_extract(transcript, ocr_text, visual_summary, labels):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"idea": "Idea A", "cluster_label": "cluster-a"}
        return {"idea": "Idea B", "cluster_label": "cluster-b"}

    channel.set_enumerate_fn(
        lambda url: [
            {"url": "http://example.com/v1", "title": "V1"},
            {"url": "http://example.com/v2", "title": "V2"},
        ]
    )
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))
    extraction.set_extract_fn(two_cluster_extract)
    verdict.set_verify_fn(tracking_verify)

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())

    assert sorted(verify_calls) == ["cluster-a", "cluster-b"]
