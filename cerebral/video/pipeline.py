"""Video pipeline -- download + transcribe + (escalate) one video (ADR-0017).

All I/O is injectable via set_download_fn / set_transcribe_fn so tests
need no network and no binaries.  Production implementations use yt-dlp
(audio-only pull) and faster-whisper (small/int8/CPU/vad_filter=True).

S2 #640 adds escalation: thin-transcript / deictic-cue or visual=True forces
OCR + vision on scene-change keyframes (see cerebral.video.escalation).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from cerebral.video import escalation as _escalation
from cerebral.video import screen_capture as _screen_capture

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
    # ponytail: live-verify only -- never called in the loop's tests
    import yt_dlp  # type: ignore[import]

    from cerebral.video.ytdlp_cookies import apply_auth

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
    }
    apply_auth(opts)  # cookies + player_client -- keep this fallback in sync with _video_download
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
    # ponytail: live-verify only -- never called in the loop's tests
    from faster_whisper import WhisperModel  # type: ignore[import]

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), vad_filter=True)
    return " ".join(s.text.strip() for s in segments)


def get_download_fn() -> Callable[[str, Path], dict]:
    return _download_fn or _prod_download


def get_transcribe_fn() -> Callable[[Path], str]:
    return _transcribe_fn or _prod_transcribe


def atempo_filter(speed: float) -> str:
    """Build an ffmpeg ``atempo`` chain for arbitrary speed (S14 #667).

    Each ``atempo`` instance is limited to 0.5-2.0, so speeds above 2.0 chain
    multiple filters (2.0 * 1.5 == 3.0 -> "atempo=2.0,atempo=1.5"). Speeding the
    audio before whisper cuts transcription time ~proportionally, since whisper
    cost scales with audio duration. Clamped to a sane [0.5, 3.0].
    """
    speed = max(0.5, min(3.0, float(speed)))
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        parts.append(f"atempo={2.0:g}")
        remaining /= 2.0
    parts.append(f"atempo={remaining:g}")
    return ",".join(parts)


# ── pipeline ──────────────────────────────────────────────────────────────────

async def run(
    url: str,
    *,
    visual: bool = False,
    force_capture: bool = False,
    budget: "_escalation.EscalationBudget | None" = None,
) -> dict[str, Any]:
    """Download + transcribe + (escalate) one video.  Returns metadata dict.

    visual=True forces the visual layers (OCR + vision) regardless of triggers.
    force_capture=True skips yt-dlp and acquires via screen-watch capture
    (S10 #658); otherwise yt-dlp is tried first and capture is the fallback when
    the download raises (e.g. TikTok's broken extractor).
    budget limits how many escalations a batch allows; None means uncapped.
    Runs blocking I/O in executor threads so Cerebral's asyncio loop stays free.
    """
    loop = asyncio.get_event_loop()
    download = get_download_fn()
    transcribe = get_transcribe_fn()
    capture = _screen_capture.get_capture_fn()

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)

        # Acquisition: yt-dlp download, UNLESS screen-watch capture is explicitly
        # forced. S18 #675: capture is opt-in only -- it opens a browser and grabs
        # the screen, so it must NEVER auto-fire on a download failure during a
        # headless batch (that spawned ~33 browser tabs overnight on YouTube 403s).
        # A download failure now propagates so the batch marks the video 'failed'.
        captured_frames: list[Path] | None = None
        if force_capture:
            meta = await loop.run_in_executor(None, capture, url, out_dir)
            captured_frames = meta.get("frames") or []
        else:
            meta = await loop.run_in_executor(None, download, url, out_dir)

        audio_path: Path = meta["audio_path"]
        transcript = await loop.run_in_executor(None, transcribe, audio_path)

        result: dict[str, Any] = {
            "title": meta.get("title", ""),
            "duration": meta.get("duration", 0.0),
            "transcript": transcript,
            "ocr_text": "",
            "visual_summary": "",
            "escalated": False,
        }

        if _escalation.needs_escalation(transcript, visual=visual):
            if budget is None or budget.consume():
                # Reuse frames from screen-watch capture when present; a captured
                # source can't be re-fetched by yt-dlp for keyframes anyway.
                # Escalation is an ENHANCEMENT (OCR + vision). If it fails -- e.g.
                # the full-video re-download 403s -- keep the transcript we already
                # have rather than nuking the whole ingest. Degrade to 'transcribed'.
                try:
                    vis = await _escalation.run(url, out_dir, frames=captured_frames)
                    result.update(vis)
                    result["escalated"] = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[video] visual escalation failed for %s, keeping transcript: %s",
                        url, exc,
                    )
            else:
                logger.info(
                    "[video] escalation cap reached, skipping visual for %s", url
                )

    return result
