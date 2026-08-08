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
import re
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from .rolling_buffer import RollingBuffer

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BLOCK_SIZE = 4_000          # 0.25 s per callback
CHUNK_SECONDS = BLOCK_SIZE / SAMPLE_RATE   # 0.25 s
WAKE_WORD = "felix"

# ── Endpointing (VAD): how Felix knows you finished talking ──────────────────
# Instead of a blind fixed window, capture stops after a stretch of trailing
# silence. ponytail: an RMS energy gate — no extra dependency. SILENCE_RMS is
# the CALIBRATION KNOB: raise it in a noisy room, lower it if Felix cuts you
# off mid-sentence. Swap for webrtcvad/silero if energy proves too blunt.
SILENCE_RMS = 500.0            # int16 RMS below this counts as silence
SILENCE_HANGOVER_S = 1.2       # trailing silence that ends one utterance
NO_SPEECH_TIMEOUT_S = 4.0      # give up if no speech at all after waking
MAX_UTTERANCE_S = 15.0         # hard cap on a single utterance
CONTINUOUS_CAP_S = 300.0       # safety cap for "keep listening" mode

# Look-back prepended to the FIRST utterance. Vosk finalises the wake word
# only after the phrase completes, so a command said in one breath
# ("Felix, what's the time?") is already over by the time we start
# capturing — without this, Whisper only sees trailing silence and
# hallucinates ("thank you"). Short on purpose (not the old 60s): just
# enough to catch the command spoken with the wake word.
PRE_WAKE_LOOKBACK_S = 4.0

# Phrases that leave an extended-listen session (case-insensitive).
STOP_PHRASES = ("felix stop", "stop listening", "stop")

# Two-stage wake confirmation. Vosk's constrained recogniser is an eager,
# cheap trigger that mis-hears ambient speech as "felix". After it fires we
# already transcribe the audio with Whisper, so we use that as the accurate
# second stage: proceed only if the wake word (or a common Whisper mishear of
# it) actually appears. Tunable — add variants if real wakes get dropped.
WAKE_CONFIRM_VARIANTS = ("felix", "felex", "feelix", "phoenix", "felicks", "felicia")


def transcript_confirms_wake(text: str) -> bool:
    """True if a first-utterance transcript actually contains the wake word,
    guarding against Vosk false positives on ambient audio."""
    t = text.lower()
    return any(v in t for v in WAKE_CONFIRM_VARIANTS)

VOSK_MODEL_PATH = Path(__file__).parent.parent / "models" / "vosk-model-small-en-us-0.15"


def _rms(chunk: np.ndarray) -> float:
    """Root-mean-square amplitude of an int16 mono chunk (0 for empty)."""
    if len(chunk) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(chunk.astype(np.float64)))))


def tail_samples(audio: np.ndarray, seconds: float, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Last ``seconds`` of an audio array (the whole thing if it's shorter)."""
    n = int(seconds * rate)
    return audio[-n:] if len(audio) > n else audio


def endpoint_reached(
    rms_values: list[float],
    chunk_dur: float = CHUNK_SECONDS,
    *,
    silence_rms: float = SILENCE_RMS,
    hangover_s: float = SILENCE_HANGOVER_S,
    no_speech_s: float = NO_SPEECH_TIMEOUT_S,
    max_s: float = MAX_UTTERANCE_S,
) -> "tuple[bool, int]":
    """Decide when one utterance ends, from a series of per-chunk RMS values.

    Pure + testable — the threading capture loop feeds this incrementally.
    Returns (ended, n_chunks_to_keep). ``ended`` is False when the series so
    far shows neither a silence-endpoint nor a timeout; the caller keeps
    collecting. Endpoints: (1) speech began, then >= hangover_s of silence;
    (2) no speech at all for no_speech_s; (3) max_s hard cap.
    """
    speech_started = False
    silent_run = 0.0
    for i, r in enumerate(rms_values):
        elapsed = (i + 1) * chunk_dur
        if r >= silence_rms:
            speech_started = True
            silent_run = 0.0
        elif speech_started:
            silent_run += chunk_dur
        if speech_started and silent_run >= hangover_s:
            return True, i + 1
        if not speech_started and elapsed >= no_speech_s:
            return True, i + 1
        if elapsed >= max_s:
            return True, i + 1
    return False, len(rms_values)


def parse_listen_directive(text: str) -> "tuple[str, float]":
    """Classify a wake command into a listen mode.

    Returns ('single', 0) for a normal one-shot command, ('timed', seconds)
    for "listen for N seconds/minutes", or ('continuous', 0) for
    "keep listening" / "listen until ...".
    """
    t = text.lower().strip()
    m = re.search(r"listen(?:ing)?\s+for\s+(\d+)\s*(second|minute)", t)
    if m:
        n = int(m.group(1))
        return "timed", float(n * (60 if m.group(2).startswith("minute") else 1))
    if any(p in t for p in ("keep listening", "listen until",
                            "stay listening", "keep talking")):
        return "continuous", 0.0
    return "single", 0.0


def is_stop_phrase(text: str) -> bool:
    """True if a transcript ends an extended-listen session."""
    t = text.lower().strip().strip(".!,")
    return t in STOP_PHRASES or "felix stop" in t

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
            logger.info("[audio] Loading Whisper distil-small.en model (first use — may download ~142 MB)...")
            _whisper_model = WhisperModel("distil-small.en", device="cpu", compute_type="int8")
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
        device: str = "",
    ) -> None:
        self._on_wake = on_wake
        self._on_passive = on_passive
        self.signal_words: list[str] = signal_words if signal_words is not None else []
        # F3 (#326): preferred input device label (empty = sounddevice default).
        # Changing this requires a pipeline restart; it is applied only in start().
        self._device: str = device
        self._buffer = RollingBuffer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream = None
        self._rec = None
        self._vosk_model = None  # Shared with voice consent (Issue #50)
        self._running = False
        self._active = False
        # Half-duplex: True while Felix's TTS is playing. The always-on mic
        # hears Felix's own voice through the speakers; without this the
        # spoken reply self-triggers the wake word / gets captured, so Felix
        # "listens all the time". Set by main.py around _speak().
        self._speaking = False
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
            device=self._device or None,
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

    def set_speaking(self, speaking: bool) -> None:
        """Half-duplex gate. main.py sets True before Felix's TTS starts and
        False a beat after it finishes, so the mic ignores Felix's own voice."""
        self._speaking = speaking

    # ── Internals ────────────────────────────────────────────────────────────

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status
    ) -> None:
        if not self._running:
            return

        # Half-duplex: while Felix is speaking, ignore the mic entirely — do
        # not buffer (would poison the look-back) and do not wake. Prevents
        # the TTS output from self-triggering through the speakers.
        if self._speaking:
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

        # In an active listen session but between utterances (transcribing a
        # previous one): stay silent so the mic can't re-wake on "felix" or
        # capture stray audio until the session ends.
        if self._active:
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
        # Snapshot the recent look-back NOW (on the audio thread) — it holds
        # the command spoken with the wake word, which Vosk only finalised
        # after the fact. Passed into the first utterance below.
        lookback = tail_samples(self._buffer.snapshot(), PRE_WAKE_LOOKBACK_S)
        threading.Thread(
            target=self._run_wake_session,
            args=(lookback,),
            daemon=True,
            name="felix-wake",
        ).start()

    def _capture_one_utterance(self) -> np.ndarray:
        """Record from the mic until the caller stops talking (endpointing).

        Registers a fresh post-wake chunk sink, polls the growing chunk list,
        and stops on a silence-endpoint / no-speech / max-duration decision
        (see ``endpoint_reached``). Returns the int16 audio up to the endpoint
        (empty array if nothing was captured). No 60s look-back — only audio
        that arrives after this call is included.
        """
        chunks: list[np.ndarray] = []
        self._post_wake_chunks = chunks
        rms_values: list[float] = []
        processed = 0
        keep = 0
        try:
            while self._running:
                time.sleep(CHUNK_SECONDS / 2)
                while processed < len(chunks):
                    rms_values.append(_rms(chunks[processed]))
                    processed += 1
                ended, keep = endpoint_reached(rms_values)
                if ended:
                    break
        finally:
            self._post_wake_chunks = None
        kept = chunks[:keep] if keep else chunks
        return (
            np.concatenate(kept) if kept else np.array([], dtype=np.int16)
        )

    def _run_wake_session(self, lookback: np.ndarray | None = None) -> None:
        """One wake -> command(s). Default is a single endpointed utterance;
        a "listen for N" / "keep listening" directive opens an extended
        session that keeps taking commands (each pause = one command) until
        the time runs out or the user says a stop phrase.

        ``lookback`` is the few seconds captured just before the wake fired
        (see ``_on_wake_detected``); it is prepended to the first utterance
        so a one-breath "Felix, <command>" is transcribed in full."""
        try:
            audio = self._capture_one_utterance()
            if lookback is not None and len(lookback):
                audio = np.concatenate([lookback, audio])
            transcript = self._transcribe(self._to_float(audio))
            logger.info("[audio] Transcript: %r", transcript)

            # Stage 2: confirm Vosk's trigger was a real wake, not ambient
            # audio mis-heard as "felix". The look-back means a genuine
            # "Felix, ..." shows the wake word here; a false wake does not.
            if not transcript_confirms_wake(transcript):
                logger.info("[audio] False wake discarded (no wake word in transcript)")
                return

            mode, seconds = parse_listen_directive(transcript)
            if mode == "single":
                self._emit_wake(transcript)
                return

            logger.info(
                "[audio] Entering %s listen mode (%.0fs)",
                mode, seconds or CONTINUOUS_CAP_S,
            )
            deadline = time.time() + (seconds if mode == "timed" else CONTINUOUS_CAP_S)
            while self._running and time.time() < deadline:
                audio = self._capture_one_utterance()
                if len(audio) == 0:
                    continue
                text = self._transcribe(self._to_float(audio))
                logger.info("[audio] Listen-mode transcript: %r", text)
                if not text:
                    continue
                if is_stop_phrase(text):
                    logger.info("[audio] Stop phrase heard — leaving listen mode")
                    break
                self._emit_wake(text)
            logger.info("[audio] Listen mode ended")
        except Exception:
            logger.exception("[audio] Error in wake session")
        finally:
            self._active = False

    @staticmethod
    def _to_float(audio_int16: np.ndarray) -> np.ndarray:
        """int16 mono -> float32 in [-1, 1) for Whisper (empty stays empty)."""
        if len(audio_int16) == 0:
            return np.array([], dtype=np.float32)
        return audio_int16.astype(np.float32) / 32_768.0

    def _emit_wake(self, transcript: str) -> None:
        """Hand a finished command transcript to the async on_wake callback."""
        if transcript and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._on_wake(transcript), self._loop)

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
        # vad_filter drops non-speech before decoding, so trailing/leading
        # silence in the captured window can't make Whisper hallucinate a
        # phantom "thank you" / "thanks for watching".
        segments, _ = model.transcribe(
            audio_float32, language="en",
            initial_prompt="Felix, Cerebral, OpenMind, Ollama, MCP, Kokoro, Vosk.",
            vad_filter=True,
        )
        return " ".join(seg.text for seg in segments).strip()
