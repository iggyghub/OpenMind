"""Tests for visual escalation -- ADR-0017 S2 #640.

No network, no binaries: all I/O goes through injectable seams.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cerebral.video import escalation
from cerebral.video import pipeline
from cerebral.video.store import VideoStore


# ── trigger detection ─────────────────────────────────────────────────────────

def test_thin_transcript_detected():
    assert escalation.is_thin("short")
    assert escalation.is_thin("")


def test_normal_transcript_not_thin():
    words = " ".join(["word"] * escalation.THIN_MIN_WORDS)
    assert not escalation.is_thin(words)


def test_deictic_cue_detected():
    assert escalation.has_deictic("Look at this technique here.")
    assert escalation.has_deictic("As you can see in the chart.")


def test_normal_transcript_no_deictic():
    assert not escalation.has_deictic("Buy low, sell high. Simple strategy.")


def test_needs_escalation_visual_force():
    rich = " ".join(["word"] * 50)  # not thin, no deictic
    assert escalation.needs_escalation(rich, visual=True)


def test_needs_escalation_thin():
    assert escalation.needs_escalation("tiny", visual=False)


def test_needs_escalation_deictic():
    assert escalation.needs_escalation("Check this out for more detail", visual=False)


def test_needs_escalation_normal_skips():
    rich = " ".join(["word"] * 50)
    assert not escalation.needs_escalation(rich, visual=False)


# ── EscalationBudget ─────────────────────────────────────────────────────────

def test_budget_consume_decrements():
    b = escalation.EscalationBudget(3)
    assert b.consume() is True
    assert b.remaining == 2


def test_budget_exhausted_returns_false():
    b = escalation.EscalationBudget(1)
    b.consume()
    assert b.consume() is False
    assert b.remaining == 0


def test_budget_zero_never_consumed():
    b = escalation.EscalationBudget(0)
    assert b.consume() is False


# ── escalation.run with stubs ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_escalation_seams():
    """Clear seams before / after each test so stubs don't bleed between tests."""
    escalation.set_keyframe_fn(None)
    escalation.set_ocr_fn(None)
    escalation.set_vision_fn(None)
    yield
    escalation.set_keyframe_fn(None)
    escalation.set_ocr_fn(None)
    escalation.set_vision_fn(None)


def test_escalation_run_returns_ocr_and_vision(tmp_path):
    frame = tmp_path / "frame001.jpg"
    frame.write_bytes(b"fake")

    escalation.set_keyframe_fn(lambda url, out: [frame])
    escalation.set_ocr_fn(lambda f: "BUY NOW")
    escalation.set_vision_fn(lambda frames: "A person holding a phone")

    result = asyncio.run(escalation.run("http://example.com/v", tmp_path))

    assert result["ocr_text"] == "BUY NOW"
    assert result["visual_summary"] == "A person holding a phone"


def test_escalation_run_discards_frames(tmp_path):
    frame = tmp_path / "frame001.jpg"
    frame.write_bytes(b"fake")

    escalation.set_keyframe_fn(lambda url, out: [frame])
    escalation.set_ocr_fn(lambda f: "")
    escalation.set_vision_fn(lambda frames: "")

    asyncio.run(escalation.run("http://example.com/v", tmp_path))

    assert not frame.exists(), "frame should be deleted after extraction"


def test_escalation_run_empty_frames(tmp_path):
    escalation.set_keyframe_fn(lambda url, out: [])
    escalation.set_ocr_fn(lambda f: "never called")
    escalation.set_vision_fn(lambda frames: "never called")

    result = asyncio.run(escalation.run("http://example.com/v", tmp_path))

    assert result["ocr_text"] == ""
    assert result["visual_summary"] == ""


# ── pipeline.run with escalation ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_pipeline_seams():
    pipeline.set_download_fn(None)
    pipeline.set_transcribe_fn(None)
    yield
    pipeline.set_download_fn(None)
    pipeline.set_transcribe_fn(None)


def _stub_download(url, out_dir):
    audio = Path(out_dir) / "audio.mp3"
    audio.write_bytes(b"fake")
    return {"audio_path": audio, "title": "Test Video", "duration": 60.0}


def test_pipeline_escalates_thin_transcript(tmp_path):
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: "short")  # thin
    escalation.set_keyframe_fn(lambda url, out: [])
    escalation.set_ocr_fn(lambda f: "")
    escalation.set_vision_fn(lambda frames: "visual desc")

    result = asyncio.run(pipeline.run("http://example.com/v"))

    assert result["escalated"] is True


def test_pipeline_escalates_deictic(tmp_path):
    rich_deictic = " ".join(["word"] * 30) + " look at this product"
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: rich_deictic)
    escalation.set_keyframe_fn(lambda url, out: [])
    escalation.set_ocr_fn(lambda f: "")
    escalation.set_vision_fn(lambda frames: "")

    result = asyncio.run(pipeline.run("http://example.com/v"))

    assert result["escalated"] is True


def test_pipeline_skips_escalation_normal_transcript():
    rich = " ".join(["word"] * 50)
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: rich)
    # Seams deliberately not set; prod funcs would fail if called
    escalation.set_keyframe_fn(lambda url, out: (_ for _ in ()).throw(AssertionError("should not escalate")))

    result = asyncio.run(pipeline.run("http://example.com/v"))

    assert result["escalated"] is False
    assert result["ocr_text"] == ""
    assert result["visual_summary"] == ""


def test_pipeline_visual_flag_forces_escalation(tmp_path):
    rich = " ".join(["word"] * 50)  # would not normally escalate
    frame = tmp_path / "frame001.jpg"
    frame.write_bytes(b"fake")
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: rich)
    escalation.set_keyframe_fn(lambda url, out: [frame])
    escalation.set_ocr_fn(lambda f: "")
    escalation.set_vision_fn(lambda frames: "forced vision")

    result = asyncio.run(pipeline.run("http://example.com/v", visual=True))

    assert result["escalated"] is True
    assert result["visual_summary"] == "forced vision"


def test_pipeline_budget_cap_prevents_escalation():
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: "short")  # thin -> would escalate
    budget = escalation.EscalationBudget(0)  # cap exhausted

    result = asyncio.run(pipeline.run("http://example.com/v", budget=budget))

    assert result["escalated"] is False


def test_pipeline_budget_consumed_on_escalation():
    pipeline.set_download_fn(_stub_download)
    pipeline.set_transcribe_fn(lambda p: "short")
    escalation.set_keyframe_fn(lambda url, out: [])
    escalation.set_ocr_fn(lambda f: "")
    escalation.set_vision_fn(lambda frames: "")
    budget = escalation.EscalationBudget(2)

    asyncio.run(pipeline.run("http://example.com/v", budget=budget))

    assert budget.remaining == 1


# ── video store S2 columns ────────────────────────────────────────────────────

def test_store_persists_ocr_and_visual(tmp_path):
    db = tmp_path / "test.db"
    store = VideoStore(db_path=db)
    store.upsert(
        "http://example.com/v",
        transcript="short",
        ocr_text="BUY NOW",
        visual_summary="Person showing product",
        escalated=True,
        stage="escalated",
    )
    v = store.get_by_url("http://example.com/v")
    assert v.ocr_text == "BUY NOW"
    assert v.visual_summary == "Person showing product"
    assert v.escalated is True
    assert v.stage == "escalated"


def test_store_migration_adds_columns(tmp_path):
    """Existing DB without S2 columns gets them added on init."""
    db = tmp_path / "test.db"
    # Create old-style table without S2 columns.
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            channel TEXT, title TEXT, duration REAL, transcript TEXT,
            stage TEXT NOT NULL DEFAULT 'enumerated',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()

    # VideoStore.__init__ should run migrations without error.
    store = VideoStore(db_path=db)
    store.upsert("http://example.com/v", stage="enumerated")
    v = store.get_by_url("http://example.com/v")
    assert v is not None
    assert v.ocr_text is None
    assert v.escalated is False
