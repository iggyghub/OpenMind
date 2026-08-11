"""Screen-watch capture backend -- ADR-0017 S10 #658.

For sources yt-dlp can't download (e.g. TikTok's broken extractor), Felix
acquires a video the way a person does: opens its browser, navigates to the
URL, plays it, and captures system audio (WASAPI loopback) -> whisper, plus
sampled frames -> the existing OCR/vision escalation layer.

This module owns only the seam + the fallback contract; the real capture
(browser + loopback audio + frame grab) is injected from cerebral/main.py via
``_wire_plugin_seams`` (it needs the orchestrator/browser, which cerebral/ must
not import directly -- seam rule #153/#385). Tests stub the seam; no real
browser or audio device is ever touched in the loop.

Contract:
  capture_fn(url, out_dir) -> {
      "audio_path": Path,   # captured system audio for the playback
      "title": str,
      "duration": float,
      "frames": list[Path], # sampled frames (may be empty)
  }
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_capture_fn: Optional[Callable[[str, Path], dict]] = None


def set_capture_fn(fn: Callable[[str, Path], dict]) -> None:
    """Inject the screen-watch capture implementation (wired from main.py)."""
    global _capture_fn
    _capture_fn = fn


def get_capture_fn() -> Callable[[str, Path], dict]:
    return _capture_fn or _prod_capture


def _prod_capture(url: str, out_dir: Path) -> dict:
    # ponytail: real capture is injected from main.py (needs browser + audio device);
    # this fallback only fires if wiring was skipped, and says so loudly.
    logger.warning("[video/screen_capture] _prod_capture fallback -- wire set_capture_fn via main.py")
    raise NotImplementedError(
        "screen capture_fn not wired; ensure _wire_plugin_seams ran (S10 #658)"
    )
