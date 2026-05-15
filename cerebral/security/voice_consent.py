"""
Voice consent surface — Issue #50, ADR-0005.

When the gate resolves to ASK in active mode and both Kokoro (TTS) and
Vosk are ready, Felix speaks the consent gist aloud and listens for one
of three words — "yes", "no", "later" — via a Vosk constrained grammar.
Whichever resolves first (this voice prompt, or the tray notification
from #48) wins the race in ``ConsentSurface``.

Choice mapping (sharpener #4 on #50):
  - "yes"   → CHOICE_ONCE   (no ACL mutation, dispatch this one call)
  - "no"    → CHOICE_DENY
  - "later" → CHOICE_DENY   (functionally identical to "no" in v1; the
                             re-queue path lives in #52)
  - timeout / "[unk]" / unrecognised → CHOICE_DENY (fail-closed)

Audio sharing (sharpener pin / handoff):
  Voice consent does NOT open a second ``sd.InputStream`` — that conflicts
  with the pipeline's exclusive Windows stream and double-reads on
  macOS/Linux. Instead it registers a tiny listener on the
  ``AudioPipeline``'s existing fan-out (#50 addition to pipeline.py).
  The listener feeds raw chunks to a per-prompt Vosk recogniser built
  with grammar ``["yes", "no", "later", "[unk]"]``.

Vosk model reuse (sharpener pin):
  The recogniser uses the same loaded ``vosk.Model`` instance that the
  AudioPipeline already holds (~40 MB) — exposed via
  ``AudioPipeline.vosk_model``. The recogniser itself is built fresh per
  prompt and discarded on exit.

Irreversible carve-out (sharpener #7, AC#7):
  The orchestrator's ``call_tool`` ladder routes ``flags.irreversible``
  to ``ModalSurface`` *before* reaching ``ConsentSurface.request``, so
  this module never sees an irreversible call. A defensive runtime check
  here would just hide a future routing bug — the invariant is pinned
  by the regression test in ``test_irreversible_modal.py``.

Fail-closed (sharpener #6, AC#6):
  When TTS or the audio pipeline are unavailable, ``ready`` is False and
  main.py simply does not wire ``voice_prompt_fn`` onto the consent
  surface — the surface degrades to tray-only without any error.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Protocol

import numpy as np

from cerebral.security.consent import CHOICE_DENY, CHOICE_ONCE, ConsentRequest
from cerebral.security.labels import label_for

logger = logging.getLogger(__name__)


# Listen for at most this long after the gist finishes speaking. Shorter
# than the tray's 30s default because the user is right there and a
# missed utterance should fall back to the tray prompt (or DENY).
DEFAULT_MAX_LISTEN_SECONDS = 8.0

# The closed three-word voice vocabulary. Plus the Vosk ``[unk]`` token
# which catches anything else — mapped to DENY in ``_map_to_choice``.
VOICE_VOCAB: tuple[str, ...] = ("yes", "no", "later")


class _VoiceRecognizerProtocol(Protocol):
    """Tiny abstraction over Vosk's KaldiRecognizer so tests can inject
    a fake without importing Vosk. Implementations feed audio chunks and
    return either ``None`` (still listening) or the recognised phrase
    string when the recogniser commits a final result."""

    def accept(self, chunk_bytes: bytes) -> str | None: ...

    def final(self) -> str: ...


class VoskRecognizer:
    """Concrete adapter around ``vosk.KaldiRecognizer`` (~3-line wrapper).

    Built fresh per prompt by ``VoiceConsent``; the underlying Vosk Model
    is owned by the AudioPipeline and reused. The grammar is the closed
    ``["yes", "no", "later", "[unk]"]`` set per sharpener #3.
    """

    def __init__(self, model, sample_rate: int, vocab: tuple[str, ...] = VOICE_VOCAB) -> None:
        # Lazy import — keeps the test environment vosk-free.
        from vosk import KaldiRecognizer  # noqa: PLC0415

        grammar = json.dumps(list(vocab) + ["[unk]"])
        self._rec = KaldiRecognizer(model, sample_rate, grammar)

    def accept(self, chunk_bytes: bytes) -> str | None:
        if self._rec.AcceptWaveform(chunk_bytes):
            result = json.loads(self._rec.Result())
            text = result.get("text", "").strip()
            return text or None
        return None

    def final(self) -> str:
        result = json.loads(self._rec.FinalResult())
        return result.get("text", "").strip()


def _map_to_choice(heard: str | None) -> str:
    """Translate a recognised utterance to the consent surface's four-verb
    vocabulary. ``yes`` → CHOICE_ONCE; anything else (no/later/[unk]/empty
    /unrecognised) → CHOICE_DENY (fail-closed)."""
    if heard is None:
        return CHOICE_DENY
    word = heard.strip().lower()
    if word == "yes":
        return CHOICE_ONCE
    # "no", "later", "[unk]", "", and any noise that slipped through fall
    # through to DENY. The user can re-trigger via the tray prompt
    # alongside, or simply restate.
    return CHOICE_DENY


class VoiceConsent:
    """Voice surface for the consent gate (Issue #50).

    Construction is cheap; the heavy work happens inside ``prompt()``:
      1. Build a per-prompt Vosk recogniser via the injected factory.
      2. Register an audio listener on the pipeline.
      3. Speak the gist via the injected TTS.
      4. Await one of "yes"/"no"/"later" with an 8s timeout.
      5. Unregister the listener (always, even on cancel/timeout/error).
      6. Map the heard word to CHOICE_ONCE or CHOICE_DENY.

    ``ConsentSurface`` races ``prompt()`` against the tray's ``prompt_fn``;
    whichever returns first wins. The race coordinator owns cancellation,
    so this module only worries about clean teardown of the listener.
    """

    def __init__(
        self,
        *,
        tts,
        audio_pipeline,
        recognizer_factory: Callable[[], _VoiceRecognizerProtocol] | None = None,
        voice_id_fn: Callable[[], str | None] | None = None,
        plugin_name_for_tool: Callable[[str], str | None] | None = None,
        max_listen_seconds: float = DEFAULT_MAX_LISTEN_SECONDS,
        sample_rate: int = 16_000,
    ) -> None:
        self._tts = tts
        self._audio = audio_pipeline
        self._voice_id_fn = voice_id_fn or (lambda: None)
        self._plugin_name_for_tool = plugin_name_for_tool or (lambda _t: None)
        self._max_listen_seconds = max_listen_seconds
        self._sample_rate = sample_rate

        if recognizer_factory is not None:
            self._recognizer_factory: Callable[[], _VoiceRecognizerProtocol] = recognizer_factory
            # When a factory is injected (real wiring or test), trust the
            # caller's readiness signalled via tts.ready and a non-None
            # audio_pipeline.
            self._ready = bool(getattr(tts, "ready", False) and audio_pipeline is not None)
        else:
            # No factory injected — try to build one from the pipeline's
            # loaded Vosk model. If the pipeline never started (no model)
            # or Vosk is not installed, ``ready`` stays False and prompt()
            # short-circuits to DENY.
            model = getattr(audio_pipeline, "vosk_model", None)
            if model is None or not getattr(tts, "ready", False):
                self._recognizer_factory = lambda: None  # type: ignore[assignment, return-value]
                self._ready = False
            else:
                sample_rate_local = sample_rate
                self._recognizer_factory = lambda: VoskRecognizer(model, sample_rate_local)
                self._ready = True

    @property
    def ready(self) -> bool:
        """True iff TTS is loaded AND a recogniser can be built. main.py
        skips ``set_voice_prompt_fn`` entirely when this is False, so a
        not-ready VoiceConsent never gets called — but ``prompt`` still
        fails closed defensively in case it does."""
        return self._ready

    def build_gist(self, req: ConsentRequest) -> str:
        """Compose the spoken gist (sharpener #2).

        Template: ``"{plugin_name} wants to {capability_label}. Yes, no, or later?"``
        ``plugin_name`` falls back to the tool name when the orchestrator
        cannot map it (e.g. tests, or the call site predates registration).
        Args are intentionally omitted from the spoken text — too noisy for
        voice; the tray prompt still carries the full preview.
        """
        plugin_name = self._plugin_name_for_tool(req.tool_name) or req.tool_name
        label = label_for(req.capability).lower()
        return f"{plugin_name} wants to {label}. Yes, no, or later?"

    async def prompt(self, req: ConsentRequest) -> str:
        """Speak the gist and listen for a one-word reply.

        Returns one of ``CHOICE_ONCE`` or ``CHOICE_DENY`` (the four-verb
        vocabulary's two relevant verbs for voice — Session and Persistent
        are tray-only by design). Any other outcome — recogniser error,
        timeout, ``[unk]``, no recognised word — maps to ``CHOICE_DENY``.

        Safe under cancellation: the listener is always unregistered, and
        the recogniser is local to this call so no state leaks across
        prompts.
        """
        if not self._ready:
            logger.debug("[voice-consent] not ready — DENY without prompting")
            return CHOICE_DENY

        recognizer = self._recognizer_factory()
        if recognizer is None:
            logger.debug("[voice-consent] recogniser factory returned None — DENY")
            return CHOICE_DENY

        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[str | None] = loop.create_future()

        def _set_result_if_pending(value: str | None) -> None:
            if not result_future.done():
                result_future.set_result(value)

        def _listener(chunk: np.ndarray) -> None:
            # Runs on the sounddevice callback thread — must be tiny and
            # never raise. Errors from the recogniser bail to DENY.
            if result_future.done():
                return
            try:
                heard = recognizer.accept(chunk.tobytes())
            except Exception:
                logger.exception("[voice-consent] recogniser raised")
                try:
                    loop.call_soon_threadsafe(_set_result_if_pending, None)
                except RuntimeError:
                    pass
                return
            if heard is not None:
                try:
                    loop.call_soon_threadsafe(_set_result_if_pending, heard)
                except RuntimeError:
                    pass

        self._audio.register_listener(_listener)
        try:
            try:
                await self._tts.speak(self.build_gist(req), self._voice_id_fn())
            except Exception:
                logger.exception("[voice-consent] TTS failed — DENY")
                return CHOICE_DENY

            # The user might have already answered during the gist (e.g.
            # "no" on hearing "Files wants to delete..."). If so, the
            # future is already done. Otherwise wait up to the deadline.
            try:
                heard = await asyncio.wait_for(
                    result_future, timeout=self._max_listen_seconds,
                )
            except asyncio.TimeoutError:
                logger.info(
                    "[voice-consent] no recognised reply within %.1fs — DENY",
                    self._max_listen_seconds,
                )
                heard = None
        finally:
            self._audio.unregister_listener(_listener)

        return _map_to_choice(heard)
