"""Tests for channel batch runner -- ADR-0017 S3 #641.

No network, no binaries: enumerate_fn and pipeline seams are fully stubbed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cerebral.video import channel, escalation, pipeline
from cerebral.video.store import VideoStore


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return VideoStore(db_path=tmp_path / "test.db")


@pytest.fixture(autouse=True)
def reset_channel_state():
    """Reset module-level batch state and seams between tests."""
    channel._state.task = None
    channel._state.stop_flag = False
    channel._state.channel_url = None
    channel._state.timings = []
    channel.set_enumerate_fn(None)
    pipeline.set_download_fn(None)
    pipeline.set_transcribe_fn(None)
    escalation.set_keyframe_fn(None)
    escalation.set_ocr_fn(None)
    escalation.set_vision_fn(None)
    yield
    channel._state.task = None
    channel._state.stop_flag = False
    channel._state.channel_url = None
    channel._state.timings = []
    channel.set_enumerate_fn(None)
    pipeline.set_download_fn(None)
    pipeline.set_transcribe_fn(None)


# ── enumerate_video (store helper) ────────────────────────────────────────────

def test_enumerate_video_inserts_new(db):
    db.enumerate_video("http://example.com/v1", channel="ch", title="V1")
    v = db.get_by_url("http://example.com/v1")
    assert v is not None
    assert v.stage == "enumerated"
    assert v.channel == "ch"
    assert v.title == "V1"


def test_enumerate_video_skips_existing(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    # advance stage via upsert
    db.upsert("http://example.com/v1", stage="transcribed")
    # re-enumerate should NOT reset stage
    db.enumerate_video("http://example.com/v1", channel="ch")
    v = db.get_by_url("http://example.com/v1")
    assert v.stage == "transcribed", "enumerate_video must not regress stage"


# ── next_enumerated ───────────────────────────────────────────────────────────

def test_next_enumerated_returns_lowest_id(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    db.enumerate_video("http://example.com/v2", channel="ch")
    v = db.next_enumerated(channel="ch")
    assert v.url == "http://example.com/v1"


def test_next_enumerated_respects_channel(db):
    db.enumerate_video("http://example.com/v1", channel="ch_a")
    db.enumerate_video("http://example.com/v2", channel="ch_b")
    v = db.next_enumerated(channel="ch_b")
    assert v.url == "http://example.com/v2"


def test_next_enumerated_none_when_empty(db):
    assert db.next_enumerated(channel="ch") is None


def test_next_enumerated_skips_transcribed(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    db.upsert("http://example.com/v1", stage="transcribed")
    assert db.next_enumerated(channel="ch") is None


# ── stage_counts ──────────────────────────────────────────────────────────────

def test_stage_counts_basic(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    db.enumerate_video("http://example.com/v2", channel="ch")
    db.upsert("http://example.com/v1", stage="transcribed")
    counts = db.stage_counts(channel="ch")
    assert counts.get("enumerated") == 1
    assert counts.get("transcribed") == 1


def test_stage_counts_all_channels(db):
    db.enumerate_video("http://example.com/v1", channel="ch_a")
    db.enumerate_video("http://example.com/v2", channel="ch_b")
    counts = db.stage_counts()
    assert counts.get("enumerated") == 2


# ── batch_start ───────────────────────────────────────────────────────────────

def _stub_pipeline(url, out_dir):
    audio = Path(out_dir) / "audio.mp3"
    audio.write_bytes(b"fake")
    return {"audio_path": audio, "title": "T", "duration": 10.0}


def test_batch_start_enumerates_and_inserts(db):
    channel.set_enumerate_fn(
        lambda url: [
            {"url": "http://example.com/v1", "title": "V1"},
            {"url": "http://example.com/v2", "title": "V2"},
        ]
    )
    pipeline.set_download_fn(_stub_pipeline)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))

    result = asyncio.run(
        channel.batch_start("http://channel.example.com", db, channel="ch", sleep_secs=0)
    )

    assert result["status"] == "started"
    assert result["enumerated"] == 2


# ── batch_resume / batch_toggle (S13 #664) ──────────────────────────────────

def test_batch_resume_no_channel_returns_no_channel(db):
    channel._state.channel_url = None
    assert channel.batch_resume(db)["status"] == "no_channel"


def test_batch_toggle_pauses_when_running(db):
    import asyncio as _asyncio

    async def _never():
        await _asyncio.sleep(9999)

    loop = _asyncio.new_event_loop()
    try:
        channel._state.task = loop.create_task(_never())
        res = channel.batch_toggle(db)
        assert res["action"] == "paused"
        assert res["status"] == "stop_requested"
        assert channel._state.stop_flag is True
        channel._state.task.cancel()
        try:
            loop.run_until_complete(channel._state.task)
        except _asyncio.CancelledError:
            pass
    finally:
        loop.close()


def test_batch_toggle_during_drain_cancels_the_pause(db):
    # A pause was requested but the current video is still draining (task running,
    # stop_flag set). A second toggle should CANCEL the stop (resume), not re-pause.
    import asyncio as _asyncio

    async def _never():
        await _asyncio.sleep(9999)

    loop = _asyncio.new_event_loop()
    try:
        channel._state.task = loop.create_task(_never())
        channel._state.stop_flag = True
        res = channel.batch_toggle(db)
        assert res["action"] == "resumed"
        assert channel._state.stop_flag is False
        channel._state.task.cancel()
        try:
            loop.run_until_complete(channel._state.task)
        except _asyncio.CancelledError:
            pass
    finally:
        loop.close()


def test_batch_resume_processes_remaining_without_reenumerating(db):
    # Two enumerated rows already in the DB (as if a prior batch enumerated them).
    db.enumerate_video("http://example.com/v1", channel="ch", title="V1")
    db.enumerate_video("http://example.com/v2", channel="ch", title="V2")

    def _enumerate_must_not_run(url):
        raise AssertionError("resume must NOT re-enumerate")

    channel.set_enumerate_fn(_enumerate_must_not_run)
    pipeline.set_download_fn(_stub_pipeline)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))

    async def _run():
        channel._state.channel_url = "ch"
        res = channel.batch_resume(db, sleep_secs=0)
        await channel._state.task  # let it drain the remaining rows
        return res

    res = asyncio.run(_run())
    assert res["status"] == "resumed"
    counts = db.stage_counts(channel="ch")
    assert counts.get("enumerated", 0) == 0  # both drained
    assert counts.get("transcribed", 0) == 2


def test_batch_start_already_running_returns_status(db):
    channel.set_enumerate_fn(lambda url: [])
    asyncio.run(channel.batch_start("http://channel.example.com", db, channel="ch", sleep_secs=0))
    # Manually mark task as still running with a never-finishing coroutine
    import asyncio as _asyncio

    async def _never():
        await _asyncio.sleep(9999)

    loop = _asyncio.new_event_loop()
    try:
        task = loop.create_task(_never())
        channel._state.task = task
        result = loop.run_until_complete(
            channel.batch_start("http://other.example.com", db, channel="ch2", sleep_secs=0)
        )
        assert result["status"] == "already_running"
        task.cancel()
        try:
            loop.run_until_complete(task)
        except _asyncio.CancelledError:
            pass
    finally:
        loop.close()


def test_batch_processes_videos_sequentially(db):
    channel.set_enumerate_fn(
        lambda url: [
            {"url": "http://example.com/v1", "title": "V1"},
            {"url": "http://example.com/v2", "title": "V2"},
        ]
    )
    pipeline.set_download_fn(_stub_pipeline)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))

    async def run():
        r = await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        # Wait for the task to finish
        if channel._state.task:
            await channel._state.task
        return r

    asyncio.run(run())

    v1 = db.get_by_url("http://example.com/v1")
    v2 = db.get_by_url("http://example.com/v2")
    assert v1 is not None and v1.stage == "transcribed"
    assert v2 is not None and v2.stage == "transcribed"


def test_batch_resumes_skips_transcribed(db):
    """Already-transcribed rows are skipped; only enumerated ones processed."""
    db.enumerate_video("http://example.com/v1", channel="ch")
    db.upsert("http://example.com/v1", stage="transcribed", transcript="existing")

    processed = []

    def fake_download(url, out_dir):
        processed.append(url)
        audio = Path(out_dir) / "audio.mp3"
        audio.write_bytes(b"fake")
        return {"audio_path": audio, "title": "T", "duration": 5.0}

    channel.set_enumerate_fn(
        lambda url: [
            {"url": "http://example.com/v1", "title": "V1"},
            {"url": "http://example.com/v2", "title": "V2"},
        ]
    )
    pipeline.set_download_fn(fake_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())

    # Only v2 should have been downloaded (v1 was already transcribed)
    assert "http://example.com/v2" in processed
    assert "http://example.com/v1" not in processed


# ── batch_stop ────────────────────────────────────────────────────────────────

def test_batch_stop_when_not_running():
    result = channel.batch_stop()
    assert result["status"] == "not_running"


def test_batch_stop_sets_flag(db):
    paused = asyncio.Event()

    async def slow_download(url, out_dir):
        paused.set()
        await asyncio.sleep(9999)

    channel.set_enumerate_fn(
        lambda url: [
            {"url": f"http://example.com/v{i}", "title": f"V{i}"} for i in range(3)
        ]
    )

    def sync_download(url, out_dir):
        audio = Path(out_dir) / "audio.mp3"
        audio.write_bytes(b"fake")
        return {"audio_path": audio, "title": "T", "duration": 5.0}

    pipeline.set_download_fn(sync_download)
    pipeline.set_transcribe_fn(lambda p: " ".join(["word"] * 30))

    async def run():
        await channel.batch_start(
            "http://channel.example.com", db, channel="ch", sleep_secs=0
        )
        stop_result = channel.batch_stop()
        assert stop_result["status"] == "stop_requested"
        if channel._state.task:
            await channel._state.task

    asyncio.run(run())
    # After stop, not still running
    assert not channel._state.is_running()


# ── batch_status ──────────────────────────────────────────────────────────────

def test_batch_status_returns_stage_counts(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    db.enumerate_video("http://example.com/v2", channel="ch")
    db.upsert("http://example.com/v1", stage="transcribed")
    channel._state.channel_url = "ch"
    status = channel.batch_status(db)
    assert status["stage_counts"].get("enumerated") == 1
    assert status["stage_counts"].get("transcribed") == 1
    assert status["running"] is False


def test_batch_status_eta_computed_after_first_video(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    channel._state.channel_url = "ch"
    channel._state.timings = [10.0, 12.0]  # simulated timings
    status = channel.batch_status(db)
    # 1 enumerated remaining * mean(10,12) = 11 seconds
    assert status["eta_seconds"] == 11


def test_batch_status_eta_null_when_no_timings(db):
    db.enumerate_video("http://example.com/v1", channel="ch")
    channel._state.channel_url = "ch"
    channel._state.timings = []
    status = channel.batch_status(db)
    assert status["eta_seconds"] is None
