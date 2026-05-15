"""
Always-on audio pipeline for Felix.

Passive mode  — Vosk listens with a constrained grammar ("[felix, [unk]]")
               for minimal CPU.  Non-wake audio is discarded silently.
Active mode   — On wake: snapshot the 60s buffer, collect a 5s post-wake
               window, transcribe everything with faster-whisper, emit the
               transcript over IPC, then return to passive.

Audio-chunk listeners (Issue #50): a sidecar fan-out lets other parts of
Cerebral share the single ``sd.InputStream`` without opening a second one
(which would conflict with the exclusive Windows stream and double-read
on macOS/Linux). Voice consent registers a listener while it is waiting
for "yes"/"no"/"later" and unregisters when the prompt resolves. Listeners
run *on the audio callback thread* — they must be tiny and exception-safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from .rolling_buffer import RollingBuffer

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BLOCK_SIZE = 4_000          # 0.25 s per callback
WAKE_WORD = "felix"
POST_WAKE_SECONDS = 5

VOSK_MODEL_PATH = Path(__file__).parent.parent / "models" / "vosk-model-small-en-us-0.15"

# Default signal words that trigger a passive 5W1H pass (beyond the wake word)
DEFAULT_SIGNAL_WORDS: list[str] = [
    "remind", "reminder", "meeting", "call", "schedule", "appointment",
    "don't forget", "make sure", "need to", "have to", "should",
]

# Module-level Whisper model cache (lazy init on first wake)
_whisper_lock = threading.Lock()
_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            logger.info("[audio] Loading Whisper tiny.en model (first use — may download ~75 MB)...")
            _whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
            logger.info("[audio] Whisper model ready")
    return _whisper_model


class AudioPipeline:
    """
    Lifecycle: construct → start(loop) → [running] → stop()

    on_wake(transcript: str) is called as a coroutine on the provided asyncio
    event loop whenever Felix is woken and a transcript is ready.
    """

    def __init__(
        self,
        on_wake: Callable[[str], Awaitable[None]],
        on_passive: Callable[[str], Awaitable[None]] | None = None,
        signal_words: list[str] | None = None,
    ) -> None:
        self._on_wake = on_wake
        self._on_passive = on_passive
        self.signal_words: list[str] = signal_words if signal_words is not None else []
        self._buffer = RollingBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream = None
        self._rec = None
        self._vosk_model = None  # Shared with voice consent (Issue #50)
        self._running = False
        self._active = False
        self._passive_active = False
        self._post_wake_chunks: list[np.ndarray] | None = None
        # Audio-chunk listeners (Issue #50). Snapshot-copied for iteration in
        # the callback so a listener can unregister itself without raising.
        self._listeners: list[Callable[[np.ndarray], None]] = []

    # ── Public ───────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Load Vosk model, open the default microphone, begin passive listening."""
        if not VOSK_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Vosk model not found at {VOSK_MODEL_PATH}\n"
                "Download it first:  python cerebral/scripts/download_models.py"
            )

        import sounddevice as sd
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)  # silence kaldi verbose output

        self._loop = loop
        self._running = True

        model = Model(str(VOSK_MODEL_PATH))
        self._vosk_model = model
        # Constrained grammar: only detect the wake word — very low CPU
        self._rec = KaldiRecognizer(model, SAMPLE_RATE, f'["{WAKE_WORD}", "[unk]"]')

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()
        # Default signal words if none were provided at construction time
        if not self.signal_words:
            self.signal_words = list(DEFAULT_SIGNAL_WORDS)
        logger.info(
            "[audio] Passive listener active — wake word: '%s', signal words: %d configured",
            WAKE_WORD, len(self.signal_words),
        )

    def stop(self) -> None:
        """Stop the microphone stream and clean up."""
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("[audio] Pipeline stopped")

    def register_listener(self, listener: Callable[[np.ndarray], None]) -> None:
        """Register a chunk listener (Issue #50).

        Each registered callable is invoked from the audio callback thread
        on every chunk with the raw int16 mono ``np.ndarray``. Idempotent —
        re-registering the same listener is a no-op so callers can register
        in setup paths without lifecycle bookkeeping.

        Listeners must be tiny and exception-safe: a slow or raising listener
        backs up the sounddevice callback queue. Exceptions are caught and
        logged but do not unregister the listener.
        """
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unregister_listener(self, listener: Callable[[np.ndarray], None]) -> None:
        """Remove a previously registered listener. Idempotent — silently
        no-ops if the listener was never registered, so the consent path
        can call this in a ``finally`` block without ordering worries."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    @property
    def vosk_model(self):
        """The loaded Vosk ``Model`` instance, or None before ``start()``.

        Shared with voice consent (Issue #50) so its KaldiRecognizer can
        reuse the same in-memory model (~40 MB) rather than loading a
        second copy. The pipeline owns the model's lifecycle — voice
        consent must not call ``Model.__del__`` or hold a reference past
        pipeline ``stop()``.
        """
        return self._vosk_model

    # ── Internals ────────────────────────────────────────────────────────────

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status
    ) -> None:
        if not self._running:
            return

        chunk = indata[:, 0]  # mono int16

        # Always keep the rolling buffer up to date
        self._buffer.extend(chunk)

        # Fan out to registered listeners (Issue #50). Snapshot via tuple so
        # a listener can register/unregister itself without mutating-during-
        # iteration errors. Listeners run on this audio thread, so exceptions
        # must not propagate or sounddevice will stop the stream.
        if self._listeners:
            for listener in tuple(self._listeners):
                try:
                    listener(chunk)
                except Exception:
                    logger.exception("[audio] chunk listener raised — continuing")

        # Collect post-wake audio while active; skip wake detection
        if self._post_wake_chunks is not None:
            self._post_wake_chunks.append(chunk.copy())
            return

        # Passive: feed Vosk for wake word / signal word detection
        if self._rec.AcceptWaveform(chunk.tobytes()):
            result = json.loads(self._rec.Result())
            text = result.get("text", "").lower()
            if WAKE_WORD in text:
                self._on_wake_detected()
            elif self._matches_signal_word(text) and not self._passive_active:
                self._on_signal_word_detected()

    def _on_wake_detected(self) -> None:
        if self._active:
            return
        self._active = True
        logger.info("[audio] '%s' detected", WAKE_WORD)

        pre_wake = self._buffer.snapshot()
        post_chunks: list[np.ndarray] = []
        self._post_wake_chunks = post_chunks

        threading.Thread(
            target=self._collect_transcribe_emit,
            args=(pre_wake, post_chunks),
            daemon=True,
            name="felix-transcribe",
        ).start()

    def _collect_transcribe_emit(
        self, pre_wake: np.ndarray, post_chunks: list[np.ndarray]
    ) -> None:
        try:
            time.sleep(POST_WAKE_SECONDS)
            self._post_wake_chunks = None  # stop collecting

            post_wake = (
                np.concatenate(post_chunks)
                if post_chunks
                else np.array([], dtype=np.int16)
            )
            combined_float = (
                np.concatenate([pre_wake, post_wake]).astype(np.float32) / 32_768.0
            )

            transcript = self._transcribe(combined_float)
            logger.info("[audio] Transcript: %r", transcript)

            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._on_wake(transcript), self._loop
                )
        except Exception:
            logger.exception("[audio] Error in transcription thread")
        finally:
            self._active = False

    def _matches_signal_word(self, text: str) -> bool:
        """Return True if any configured signal word appears in text (case-insensitive)."""
        lower = text.lower()
        return any(word.lower() in lower for word in self.signal_words)

    def _on_signal_word_detected(self) -> None:
        """Vosk detected a signal word — start a passive transcription pass."""
        self._passive_active = True
        logger.info("[audio] Signal word detected — starting passive transcription")

        snapshot = self._buffer.snapshot()
        self._buffer.clear()

        loop = self._loop
        on_passive = self._on_passive
        if loop is None or on_passive is None:
            self._passive_active = False
            return

        threading.Thread(
            target=self._passive_transcribe_emit,
            args=(snapshot, loop, on_passive),
            daemon=True,
            name="felix-passive-transcribe",
        ).start()

    def _passive_transcribe_emit(
        self,
        snapshot: np.ndarray,
        loop: asyncio.AbstractEventLoop,
        on_passive: Callable[[str], Awaitable[None]],
    ) -> None:
        try:
            if len(snapshot) == 0:
                return
            audio_float = snapshot.astype(np.float32) / 32_768.0
            transcript = self._transcribe(audio_float)
            logger.info("[audio] Passive transcript: %r", transcript)
            if transcript:
                asyncio.run_coroutine_threadsafe(on_passive(transcript), loop)
        except Exception:
            logger.exception("[audio] Error in passive transcription thread")
        finally:
            self._passive_active = False

    async def _on_signal_detected(self, transcript_hint: str = "") -> None:
        """Testable entry point: snapshot, clear buffer, transcribe, call on_passive."""
        snapshot = self._buffer.snapshot()
        self._buffer.clear()

        transcript = transcript_hint if transcript_hint else self._transcribe(
            snapshot.astype(np.float32) / 32_768.0 if len(snapshot) > 0 else np.array([], dtype=np.float32)
        )
        if transcript and self._on_passive is not None:
            await self._on_passive(transcript)

    @staticmethod
    def _transcribe(audio_float32: np.ndarray) -> str:
        model = _get_whisper_model()
        segments, _ = model.transcribe(audio_float32, language="en")
        return " ".join(seg.text for seg in segments).strip()
