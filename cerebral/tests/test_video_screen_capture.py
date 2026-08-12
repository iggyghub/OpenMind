"""Screen-watch capture fallback -- ADR-0017 S10 #658.

No real browser/audio/network: the capture seam is stubbed. Verifies that
yt-dlp download failure falls back to screen-watch capture, that force_capture
skips yt-dlp, and that captured frames feed escalation instead of a keyframe pull.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cerebral.video import escalation, pipeline, screen_capture


@pytest.fixture(autouse=True)
def _reset_seams():
    yield
    for setter, mod in (
        ("_download_fn", pipeline), ("_transcribe_fn", pipeline),
    ):
        setattr(mod, setter, None)
    screen_capture.set_capture_fn(None)
    escalation.set_keyframe_fn(None)
    escalation.set_ocr_fn(None)
    escalation.set_vision_fn(None)


def _fake_capture(frames=None):
    def cap(url, out_dir):
        return {
            "audio_path": Path(out_dir) / "capture.wav",
            "title": "captured",
            "duration": 30.0,
            "frames": frames if frames is not None else [],
        }
    return cap


def test_download_failure_raises_and_does_not_capture():
    # S18 #675: a download failure must NOT auto-open screen-capture (which spawns
    # a browser tab). It propagates so the batch marks the video 'failed'.
    captured = {"called": False}

    def _boom(url, out_dir):
        raise RuntimeError("yt-dlp: HTTP Error 403: Forbidden")

    def _cap(url, out_dir):
        captured["called"] = True
        return {"audio_path": Path(out_dir) / "capture.wav", "title": "cap", "duration": 1.0, "frames": []}

    pipeline.set_download_fn(_boom)
    screen_capture.set_capture_fn(_cap)
    pipeline.set_transcribe_fn(lambda p: "x")

    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(pipeline.run("https://www.youtube.com/shorts/abc"))
    assert captured["called"] is False, "capture must not fire on a download failure"


def test_force_capture_skips_download():
    calls = {"download": 0, "capture": 0}

    def _dl(url, out_dir):
        calls["download"] += 1
        return {"audio_path": Path(out_dir) / "a.mp3", "title": "yt", "duration": 1.0}

    def _cap(url, out_dir):
        calls["capture"] += 1
        return {"audio_path": Path(out_dir) / "capture.wav", "title": "cap", "duration": 1.0, "frames": []}

    pipeline.set_download_fn(_dl)
    screen_capture.set_capture_fn(_cap)
    pipeline.set_transcribe_fn(lambda p: "words " * 30)

    meta = asyncio.run(pipeline.run("https://x/y", force_capture=True))
    assert calls["download"] == 0 and calls["capture"] == 1
    assert meta["title"] == "cap"


def test_captured_frames_feed_escalation_without_keyframe_pull():
    keyframe_called = {"n": 0}

    def _keyframe(url, out_dir):
        keyframe_called["n"] += 1
        return []

    fake_frames = [Path("/nonexistent/frame0.jpg"), Path("/nonexistent/frame1.jpg")]

    screen_capture.set_capture_fn(_fake_capture(frames=fake_frames))
    pipeline.set_transcribe_fn(lambda p: "music")  # thin -> triggers escalation
    escalation.set_keyframe_fn(_keyframe)
    escalation.set_ocr_fn(lambda f: "on-screen text")
    escalation.set_vision_fn(lambda frames: "a person shows a product")

    # S18 #675: capture is reached via force_capture now (not a download-failure fallback).
    meta = asyncio.run(pipeline.run("https://www.tiktok.com/@x/video/2", force_capture=True))
    assert meta["escalated"] is True
    assert keyframe_called["n"] == 0, "captured frames should be used, not a keyframe pull"
    assert "on-screen text" in meta["ocr_text"]
    assert meta["visual_summary"] == "a person shows a product"
