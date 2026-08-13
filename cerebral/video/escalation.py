"""Visual escalation for the video primitive -- ADR-0017 S2 #640.

Thin-transcript / deictic-cue triggers lead to OCR + vision on keyframes.
All I/O is injectable: set_keyframe_fn, set_ocr_fn, set_vision_fn.
Frames are discarded after extraction (never persisted to disk long-term).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Deictic cue phrases -- plain substring scan, no LLM (ADR-0017 decision 3).
DEICTIC_CUES: frozenset[str] = frozenset([
    "look at this", "as you can see", "shown here", "watch this",
    "look here", "as shown", "you can see", "check this out",
    "over here", "right here", "notice here", "look how",
])

THIN_MIN_WORDS: int = 20  # fewer words => thin transcript

MAX_KEYFRAMES: int = 12   # ffmpeg scene-change cap

# ── escalation triggers ───────────────────────────────────────────────────────

def is_thin(transcript: str) -> bool:
    return len(transcript.strip().split()) < THIN_MIN_WORDS


def has_deictic(transcript: str) -> bool:
    lower = transcript.lower()
    return any(cue in lower for cue in DEICTIC_CUES)


def needs_escalation(transcript: str, *, visual: bool = False) -> bool:
    """True when the visual layers should fire for this transcript."""
    if visual:
        return True
    return is_thin(transcript) or has_deictic(transcript)


# ── per-batch escalation cap ──────────────────────────────────────────────────

class EscalationBudget:
    """Mutable counter; S3 creates one per batch run, passes it to pipeline.run."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.remaining = cap

    def consume(self) -> bool:
        """Decrement and return True if budget was available; False if exhausted."""
        if self.remaining > 0:
            self.remaining -= 1
            return True
        return False


# ── injectable seams ──────────────────────────────────────────────────────────
#
# keyframe_fn(url, out_dir) -> list[Path]  -- extracts up to MAX_KEYFRAMES frames
# ocr_fn(frame_path)        -> str         -- OCR text for one frame
# vision_fn(frames)         -> str         -- short description of the frames

_keyframe_fn: Optional[Callable[[str, Path], list]] = None
_ocr_fn: Optional[Callable[[Path], str]] = None
_vision_fn: Optional[Callable[[list], str]] = None


def set_keyframe_fn(fn: Callable[[str, Path], list]) -> None:
    global _keyframe_fn
    _keyframe_fn = fn


def set_ocr_fn(fn: Callable[[Path], str]) -> None:
    global _ocr_fn
    _ocr_fn = fn


def set_vision_fn(fn: Callable[[list], str]) -> None:
    global _vision_fn
    _vision_fn = fn


def get_keyframe_fn() -> Callable:
    return _keyframe_fn or _prod_keyframe


def get_ocr_fn() -> Callable:
    return _ocr_fn or _prod_ocr


def get_vision_fn() -> Callable:
    return _vision_fn or _prod_vision


# ── production implementations (live-verify only; never called in tests) ──────

def _prod_keyframe(url: str, out_dir: Path) -> list[Path]:
    # ponytail: live-verify only -- downloads video, extracts scene-change frames, deletes video
    import subprocess  # noqa: PLC0415

    from cerebral.video.ytdlp_cookies import cookies_cli_args  # noqa: PLC0415

    vid_path = out_dir / "video.mp4"
    subprocess.run(
        [
            "yt-dlp",
            "-f", "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            *cookies_cli_args(),  # authenticate -- YouTube 403s anonymous video pulls
            url, "-o", str(vid_path), "-q",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-i", str(vid_path),
            "-vf", f"select=gt(scene\\,0.4),scale=960:-1",
            "-vsync", "vfr",
            "-frames:v", str(MAX_KEYFRAMES),
            str(out_dir / "frame%03d.jpg"),
            "-loglevel", "error",
        ],
        check=True,
    )
    vid_path.unlink(missing_ok=True)  # keep frames, discard video
    return sorted(out_dir.glob("frame*.jpg"))


def _prod_ocr(frame: Path) -> str:
    # ponytail: live-verify only
    import pytesseract  # type: ignore[import]
    from PIL import Image  # type: ignore[import]

    return pytesseract.image_to_string(Image.open(frame))


def _prod_vision(frames: list[Path]) -> str:
    # ponytail: live-verify only -- routes through model priority (Budd-VL -> fallback)
    # Real routing mirrors cerebral/main.py _computer_use_vision_ground seam (ADR-0016 sec 5).
    logger.warning("[video] _prod_vision not yet wired to model priority; returning empty")
    return ""


# ── async runner ──────────────────────────────────────────────────────────────

async def run(
    url: str, out_dir: Path, frames: "list[Path] | None" = None
) -> dict[str, str]:
    """Run OCR + vision on keyframes, discard frames.

    frames: pre-captured frames (S10 #658 screen-watch) -- when provided they are
    used directly and no yt-dlp keyframe pull happens (a screen-captured source
    can't be re-fetched anyway). When None, scene-change keyframes are extracted.
    """
    loop = asyncio.get_event_loop()

    if frames is None:
        frames = await loop.run_in_executor(None, get_keyframe_fn(), url, out_dir)
    if not frames:
        logger.warning("[video] no keyframes extracted for %s", url)
        return {"ocr_text": "", "visual_summary": ""}

    ocr_parts: list[str] = []
    for frame in frames:
        text: str = await loop.run_in_executor(None, get_ocr_fn(), frame)
        if text.strip():
            ocr_parts.append(text.strip())

    visual_summary: str = await loop.run_in_executor(None, get_vision_fn(), frames)

    # Discard frames -- raw frames are never persisted (ADR-0017 SAFETY).
    for frame in frames:
        try:
            frame.unlink()
        except OSError:
            pass

    return {
        "ocr_text": "\n".join(ocr_parts),
        "visual_summary": visual_summary or "",
    }
