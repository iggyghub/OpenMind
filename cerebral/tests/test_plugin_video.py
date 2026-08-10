"""Video plugin unit tests — S1 #639.

All network / binary I/O is replaced by injectable seams.
No real yt-dlp, no faster-whisper, no network calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cerebral.video.store import VideoStore
import plugins.video as video_mod
from plugins.video import VideoPlugin


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_store() -> VideoStore:
    return VideoStore(db_path=Path(":memory:"))


def _wire(store: VideoStore, *, title="Test Video", duration=60.0, transcript="hello world") -> VideoPlugin:
    plugin = VideoPlugin()
    video_mod.set_store(store)
    video_mod.set_download_fn(lambda url, out_dir: {
        "audio_path": out_dir / "fake.mp3",
        "title": title,
        "duration": duration,
    })
    video_mod.set_transcribe_fn(lambda path: transcript)
    # S2 #640: stub escalation seams so short test transcripts don't call prod yt-dlp
    video_mod.set_keyframe_fn(lambda url, out: [])
    video_mod.set_ocr_fn(lambda f: "")
    video_mod.set_vision_fn(lambda frames: "")
    return plugin


# ── store unit tests ──────────────────────────────────────────────────────────

def test_store_upsert_and_get_by_id():
    s = _make_store()
    vid_id = s.upsert("https://example.com/v1", title="My Video", stage="transcribed")
    v = s.get_by_id(vid_id)
    assert v is not None
    assert v.url == "https://example.com/v1"
    assert v.title == "My Video"
    assert v.stage == "transcribed"


def test_store_get_by_url():
    s = _make_store()
    s.upsert("https://example.com/v2", stage="enumerated")
    v = s.get_by_url("https://example.com/v2")
    assert v is not None
    assert v.stage == "enumerated"


def test_store_upsert_idempotent_keeps_existing_channel():
    s = _make_store()
    s.upsert("https://example.com/v3", channel="mychan", stage="enumerated")
    # Second upsert without channel — channel must be preserved (COALESCE)
    s.upsert("https://example.com/v3", stage="transcribed")
    v = s.get_by_url("https://example.com/v3")
    assert v.channel == "mychan"
    assert v.stage == "transcribed"


def test_store_missing_id_returns_none():
    s = _make_store()
    assert s.get_by_id(9999) is None


def test_store_missing_url_returns_none():
    s = _make_store()
    assert s.get_by_url("https://does-not-exist.example.com") is None


# ── video_ingest AC1: ingest and persist ──────────────────────────────────────

async def test_video_ingest_stores_transcript():
    store = _make_store()
    # Rich enough transcript (>= THIN_MIN_WORDS) so escalation does not trigger.
    rich = "buy crypto now " * 5  # 15 words -- wait, need 20+
    rich = " ".join(["word"] * 25)
    plugin = _wire(store, title="My Tiktok", transcript=rich)
    result = await plugin.call_tool("video_ingest", {"url": "https://tiktok.com/v/1"})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["stage"] == "transcribed"
    assert data["title"] == "My Tiktok"
    assert data["transcript_length"] == len(rich)

    v = store.get_by_url("https://tiktok.com/v/1")
    assert v is not None
    assert v.transcript == rich
    assert v.stage == "transcribed"


# ── video_ingest AC2: idempotent ──────────────────────────────────────────────

async def test_video_ingest_idempotent():
    store = _make_store()
    call_count = {"n": 0}

    def counting_download(url, out_dir):
        call_count["n"] += 1
        return {"audio_path": out_dir / "fake.mp3", "title": "Once", "duration": 10.0}

    plugin = VideoPlugin()
    video_mod.set_store(store)
    video_mod.set_download_fn(counting_download)
    video_mod.set_transcribe_fn(lambda p: "transcript text")
    video_mod.set_keyframe_fn(lambda url, out: [])
    video_mod.set_ocr_fn(lambda f: "")
    video_mod.set_vision_fn(lambda frames: "")

    await plugin.call_tool("video_ingest", {"url": "https://tiktok.com/v/2"})
    result2 = await plugin.call_tool("video_ingest", {"url": "https://tiktok.com/v/2"})

    assert not result2.is_error
    data = json.loads(result2.content)
    assert data["skipped"] is True
    assert call_count["n"] == 1


# ── video_get ─────────────────────────────────────────────────────────────────

async def test_video_get_returns_stored_video():
    store = _make_store()
    plugin = _wire(store)
    ingest_result = await plugin.call_tool("video_ingest", {"url": "https://yt.be/abc"})
    vid_id = json.loads(ingest_result.content)["id"]

    get_result = await plugin.call_tool("video_get", {"id": vid_id})
    assert not get_result.is_error
    data = json.loads(get_result.content)
    assert data["id"] == vid_id
    assert data["url"] == "https://yt.be/abc"


async def test_video_get_missing_returns_error():
    store = _make_store()
    video_mod.set_store(store)
    plugin = VideoPlugin()
    result = await plugin.call_tool("video_get", {"id": 9999})
    assert result.is_error


# ── AC3: nothing held in memory across videos (state in store) ────────────────

async def test_ingest_commits_per_video_no_memory_held():
    """Two separate ingest calls each produce their own committed row."""
    store = _make_store()
    plugin = _wire(store, transcript="t1")
    await plugin.call_tool("video_ingest", {"url": "https://example.com/a"})

    video_mod.set_transcribe_fn(lambda p: "t2")
    await plugin.call_tool("video_ingest", {"url": "https://example.com/b"})

    va = store.get_by_url("https://example.com/a")
    vb = store.get_by_url("https://example.com/b")
    assert va.transcript == "t1"
    assert vb.transcript == "t2"


# ── pipeline error handling ───────────────────────────────────────────────────

async def test_video_ingest_error_returns_error_result():
    store = _make_store()
    video_mod.set_store(store)

    def failing_download(url, out):
        raise RuntimeError("network down")

    video_mod.set_download_fn(failing_download)
    video_mod.set_transcribe_fn(lambda p: "")
    plugin = VideoPlugin()
    result = await plugin.call_tool("video_ingest", {"url": "https://bad.example.com/x"})
    assert result.is_error
    assert "network down" in result.content or "Ingest failed" in result.content


# ── missing url ───────────────────────────────────────────────────────────────

async def test_video_ingest_missing_url_returns_error():
    store = _make_store()
    video_mod.set_store(store)
    plugin = VideoPlugin()
    result = await plugin.call_tool("video_ingest", {})
    assert result.is_error


# ── plugin metadata ───────────────────────────────────────────────────────────

def test_plugin_lists_tools():
    plugin = VideoPlugin()
    tools = plugin.list_tools()
    names = {t.name for t in tools}
    assert "video_ingest" in names
    assert "video_get" in names


def test_plugin_required_capabilities_declared():
    """Orchestrator rejects plugins without REQUIRED_CAPABILITIES."""
    import plugins.video as vm
    assert hasattr(vm, "REQUIRED_CAPABILITIES")
    assert "external_data_read" in vm.REQUIRED_CAPABILITIES
