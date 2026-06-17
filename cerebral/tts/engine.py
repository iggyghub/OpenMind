"""
Kokoro local TTS engine for Felix.

speak(text, voice_id) — synthesise and play audio in a thread so the
asyncio event loop is never blocked.

list_voices() — returns the static Kokoro voice catalogue.

If kokoro is not installed or fails to load, the engine degrades silently
(same pattern as the audio pipeline with a missing Vosk model).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
SAMPLE_RATE = 24_000

# Static Kokoro v1.9+ voice catalogue — covers all bundled voices.
_VOICES: list[dict] = [
    {"id": "af_alloy",    "name": "Alloy",    "accent": "American", "gender": "Female"},
    {"id": "af_aoede",    "name": "Aoede",    "accent": "American", "gender": "Female"},
    {"id": "af_bella",    "name": "Bella",    "accent": "American", "gender": "Female"},
    {"id": "af_heart",    "name": "Heart",    "accent": "American", "gender": "Female"},
    {"id": "af_jadde",    "name": "Jadde",    "accent": "American", "gender": "Female"},
    {"id": "af_kore",     "name": "Kore",     "accent": "American", "gender": "Female"},
    {"id": "af_nicole",   "name": "Nicole",   "accent": "American", "gender": "Female"},
    {"id": "af_nova",     "name": "Nova",     "accent": "American", "gender": "Female"},
    {"id": "af_river",    "name": "River",    "accent": "American", "gender": "Female"},
    {"id": "af_sarah",    "name": "Sarah",    "accent": "American", "gender": "Female"},
    {"id": "af_sky",      "name": "Sky",      "accent": "American", "gender": "Female"},
    {"id": "am_adam",     "name": "Adam",     "accent": "American", "gender": "Male"},
    {"id": "am_echo",     "name": "Echo",     "accent": "American", "gender": "Male"},
    {"id": "am_eric",     "name": "Eric",     "accent": "American", "gender": "Male"},
    {"id": "am_fenrir",   "name": "Fenrir",   "accent": "American", "gender": "Male"},
    {"id": "am_liam",     "name": "Liam",     "accent": "American", "gender": "Male"},
    {"id": "am_michael",  "name": "Michael",  "accent": "American", "gender": "Male"},
    {"id": "am_onyx",     "name": "Onyx",     "accent": "American", "gender": "Male"},
    {"id": "am_orion",    "name": "Orion",    "accent": "American", "gender": "Male"},
    {"id": "am_santa",    "name": "Santa",    "accent": "American", "gender": "Male"},
    {"id": "bf_alice",    "name": "Alice",    "accent": "British",  "gender": "Female"},
    {"id": "bf_emma",     "name": "Emma",     "accent": "British",  "gender": "Female"},
    {"id": "bf_isabella", "name": "Isabella", "accent": "British",  "gender": "Female"},
    {"id": "bf_lily",     "name": "Lily",     "accent": "British",  "gender": "Female"},
    {"id": "bm_daniel",   "name": "Daniel",   "accent": "British",  "gender": "Male"},
    {"id": "bm_fable",    "name": "Fable",    "accent": "British",  "gender": "Male"},
    {"id": "bm_george",   "name": "George",   "accent": "British",  "gender": "Male"},
    {"id": "bm_lewis",    "name": "Lewis",    "accent": "British",  "gender": "Male"},
]


class TTSEngine:
    """
    Wraps Kokoro KPipeline.  Thread-safe; speak() is async-safe via executor.

    Degrades gracefully if kokoro is not installed — ready == False and all
    speak() calls are no-ops with a warning log.
    """

    def __init__(self) -> None:
        self._pipeline = None
        self._lock = threading.Lock()   # only one utterance at a time
        self._ready = False
        self._load()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            from kokoro import KPipeline  # noqa: PLC0415
            # lang_code='a' → American English (covers all af_* / am_* voices)
            self._pipeline = KPipeline(lang_code="a")
            self._ready = True
            logger.info("[tts] Kokoro engine ready (default voice: %s)", DEFAULT_VOICE)
        except ImportError:
            logger.warning(
                "[tts] kokoro not installed — TTS disabled. "
                "Run: pip install kokoro soundfile"
            )
        except Exception as exc:
            logger.warning("[tts] Kokoro failed to load: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._ready

    def list_voices(self) -> list[dict]:
        """Return all Kokoro voices with id, name, accent, gender."""
        return list(_VOICES)

    async def speak(
        self,
        text: str,
        voice_id: Optional[str] = None,
        volume: float = 1.0,
    ) -> None:
        """
        Synthesise text and play on the default output device.
        Returns only after playback finishes.  Never blocks the event loop.
        volume is clamped to [0.0, 1.0]; 0.0 = silent, 1.0 = full.
        """
        if not self._ready:
            logger.warning("[tts] speak() skipped — engine not ready")
            return
        voice = voice_id or DEFAULT_VOICE
        vol = max(0.0, min(1.0, float(volume)))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._speak_sync, text, voice, vol)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _speak_sync(self, text: str, voice_id: str, volume: float = 1.0) -> None:
        """Runs in a thread-pool thread; safe to call sounddevice blocking APIs."""
        if self._pipeline is None:
            return
        try:
            import sounddevice as sd  # noqa: PLC0415

            with self._lock:
                gen = self._pipeline(
                    text,
                    voice=voice_id,
                    speed=DEFAULT_SPEED,
                    split_pattern=r"\n+",
                )
                for _graphemes, _phonemes, audio in gen:
                    if volume != 1.0:
                        audio = audio * volume
                    sd.play(audio, samplerate=SAMPLE_RATE, blocking=True)
        except Exception as exc:
            logger.error("[tts] Playback error: %s", exc)
