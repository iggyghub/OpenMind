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


@pytest.fixture(autouse=True)
def _reset_seams():
    # Seams are module-level globals; reset to None (prod fallback) before each
    # test so a stub set in one test can't leak into the next (e.g. an extract
    # stub silently clustering a video another test expects to stay 'transcribed').
    setters = (
        video_mod.set_download_fn, video_mod.set_transcribe_fn,
        video_mod.set_keyframe_fn, video_mod.set_ocr_fn, video_mod.set_vision_fn,
        video_mod.set_extract_fn, video_mod.set_verify_fn,
    )
    for s in setters:
        s(None)
    yield
    for s in setters:
        s(None)


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
    # Stub extraction so the first ingest fully clusters (-> 'verified'); the
    # second call must then hit the already-clustered skip path.
    async def fake_extract(t, o, v, labels, category=""):
        return {"idea": "an idea", "cluster_label": "Ideas", "people_required": 1}
    video_mod.set_extract_fn(fake_extract)

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


async def test_video_ingest_escalation_failure_keeps_transcript():
    # A thin transcript (< THIN_MIN_WORDS) triggers visual escalation, which
    # re-downloads the full video for keyframes. That download can 403 (the #17013
    # case). Escalation is an enhancement: its failure must degrade to a stored
    # 'transcribed', not nuke the whole ingest and leave the video stuck.
    store = _make_store()
    plugin = _wire(store, transcript="look at this")  # thin + deictic -> escalates
    def boom(url, out):
        raise RuntimeError("HTTP 403 Forbidden")
    video_mod.set_keyframe_fn(boom)
    result = await plugin.call_tool("video_ingest", {"url": "https://yt/thin"})
    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["stage"] == "transcribed"
    assert data["escalated"] is False
    assert store.get_by_url("https://yt/thin").stage == "transcribed"


async def test_video_ingest_categorizes_into_collection():
    # Single ingest with a category extracts the idea into that collection so the
    # cluster shows under it in the panel (the "categorize single videos" gap).
    store = _make_store()
    plugin = _wire(store, transcript=" ".join(["word"] * 30))  # rich -> no escalation
    async def fake_extract(t, o, v, labels, category=""):
        return {"idea": "make a harness", "cluster_label": "Harness tips", "people_required": 1}
    video_mod.set_extract_fn(fake_extract)

    result = await plugin.call_tool(
        "video_ingest", {"url": "https://yt/h", "category": "harness improvement"}
    )
    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["stage"] == "verified"
    assert data["collection"] == "harness improvement"
    clusters = store.list_clusters("harness improvement")
    assert [c["label"] for c in clusters] == ["Harness tips"]
    assert store.get_by_url("https://yt/h").collection == "harness improvement"


async def test_video_ingest_blank_category_defaults_uncategorised():
    store = _make_store()
    plugin = _wire(store, transcript=" ".join(["word"] * 30))
    async def fake_extract(t, o, v, labels, category=""):
        return {"idea": "some idea", "cluster_label": "Misc", "people_required": 1}
    video_mod.set_extract_fn(fake_extract)

    result = await plugin.call_tool("video_ingest", {"url": "https://yt/u"})
    data = json.loads(result.content)
    assert data["collection"] == "Uncategorised"
    assert [c["label"] for c in store.list_clusters("Uncategorised")] == ["Misc"]


# ── manual move: store.move_cluster + list_collections ────────────────────────

def test_move_cluster_simple():
    s = _make_store()
    vid = s.upsert("https://v/1", collection="Uncategorised", stage="transcribed")
    cid = s.get_or_create_cluster("Idea A", "Uncategorised")
    s.upsert_idea(vid, "an idea", cid)
    survivor = s.move_cluster(cid, "harness improvement")
    assert survivor == cid
    assert [c["label"] for c in s.list_clusters("harness improvement")] == ["Idea A"]
    assert s.list_clusters("Uncategorised") == []
    # The video followed the cluster.
    assert s.get_by_url("https://v/1").collection == "harness improvement"


def test_move_cluster_merges_on_label_collision():
    s = _make_store()
    v1 = s.upsert("https://v/1", stage="transcribed")
    src = s.get_or_create_cluster("Shared", "Uncategorised")
    s.upsert_idea(v1, "idea one", src)
    v2 = s.upsert("https://v/2", stage="transcribed")
    dst = s.get_or_create_cluster("Shared", "harness improvement")
    s.upsert_idea(v2, "idea two", dst)
    survivor = s.move_cluster(src, "harness improvement")
    assert survivor == dst  # merged into the existing target
    labels = [c["label"] for c in s.list_clusters("harness improvement")]
    assert labels == ["Shared"]
    assert s.list_clusters("Uncategorised") == []


def test_move_cluster_missing_returns_none():
    assert _make_store().move_cluster(9999, "x") is None


def test_list_collections():
    s = _make_store()
    s.get_or_create_cluster("A", "money-making idea")
    s.get_or_create_cluster("B", "harness improvement")
    assert s.list_collections() == ["harness improvement", "money-making idea"]


async def test_video_move_cluster_tool():
    store = _make_store()
    video_mod.set_store(store)
    plugin = VideoPlugin()
    vid = store.upsert("https://v/1", collection="Uncategorised", stage="transcribed")
    cid = store.get_or_create_cluster("Idea A", "Uncategorised")
    store.upsert_idea(vid, "an idea", cid)
    result = await plugin.call_tool(
        "video_move_cluster", {"cluster_id": cid, "collection": "harness improvement"}
    )
    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["collection"] == "harness improvement"
    assert data["merged"] is False
    assert [c["label"] for c in store.list_clusters("harness improvement")] == ["Idea A"]


async def test_video_move_cluster_missing_args():
    store = _make_store()
    video_mod.set_store(store)
    plugin = VideoPlugin()
    r = await plugin.call_tool("video_move_cluster", {"cluster_id": 1})
    assert r.is_error


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
