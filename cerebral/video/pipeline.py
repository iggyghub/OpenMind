"""Video pipeline — download + transcribe one video (ADR-0017 S1 #639).

All I/O is injectable via set_download_fn / set_transcribe_fn so tests
need no network and no binaries.  Production implementations use yt-dlp
(audio-only pull) and faster-whisper (small/int8/CPU/vad_filter=True).

Both are live-verify items: the real run goes through docs/video-live-verify.md.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── injectable seams ──────────────────────────────────────────────────────────

_download_fn: Optional[Callable[[str, Path], dict]] = None
_transcribe_fn: Optional[Callable[[Path], str]] = None


def set_download_fn(fn: Callable[[str, Path], dict]) -> None:
    """Inject download implementation.

    fn(url, out_dir) -> {"audio_path": Path, "title": str, "duration": float}
    Production: yt-dlp audio-only pull.
    """
    global _download_fn
    _download_fn = fn


def set_transcribe_fn(fn: Callable[[Path], str]) -> None:
    """Inject transcription implementation.

    fn(audio_path) -> transcript str
    Production: faster-whisper small/int8/CPU/vad_filter=True.
    """
    global _transcribe_fn
    _transcribe_fn = fn


# ── production stubs (wired by _wire_plugin_seams, never called in tests) ─────

def _prod_download(url: str, out_dir: Path) -> dict:
    # ponytail: live-verify only — never called in the loop's tests
    import yt_dlp  # type: ignore[import]

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    title = info.get("title", "")
    duration = float(info.get("duration") or 0)
    # yt-dlp names the output after the id; find it
    audio_path = next(out_dir.glob("*.mp3"), None)
    if audio_path is None:
        raise RuntimeError(f"yt-dlp produced no mp3 in {out_dir}")
    return {"audio_path": audio_path, "title": title, "duration": duration}


def _prod_transcribe(audio_path: Path) -> str:
    # ponytail: live-verify only — never called in the loop's tests
    from faster_whisper import WhisperModel  # type: ignore[import]

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), vad_filter=True)
    return " ".join(s.text.strip() for s in segments)


def get_download_fn() -> Callable[[str, Path], dict]:
    return _download_fn or _prod_download


def get_transcribe_fn() -> Callable[[Path], str]:
    return _transcribe_fn or _prod_transcribe


# ── pipeline ──────────────────────────────────────────────────────────────────

async def run(url: str) -> dict[str, Any]:
    """Download + transcribe one video.  Returns metadata dict.

    Runs blocking I/O in executor threads so Cerebral's asyncio loop stays free.
    """
    download = get_download_fn()
    transcribe = get_transcribe_fn()

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        meta = await asyncio.get_event_loop().run_in_executor(
            None, download, url, out_dir
        )
        audio_path: Path = meta["audio_path"]
        transcript = await asyncio.get_event_loop().run_in_executor(
            None, transcribe, audio_path
        )

    return {
        "title": meta.get("title", ""),
        "duration": meta.get("duration", 0.0),
        "transcript": transcript,
    }
