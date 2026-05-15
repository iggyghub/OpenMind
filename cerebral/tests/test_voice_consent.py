"""
Voice consent surface tests — Issue #50.

Covers the ``VoiceConsent`` helper module: choice mapping
("yes" → CHOICE_ONCE, others → CHOICE_DENY), the audio-listener lifecycle
(registered exactly once, unregistered on every exit path), gist template,
fail-closed paths (TTS not ready / no recogniser / timeout / [unk]), and
the cancellation-safety contract (loser cleanup runs).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.security import (
    CHOICE_DENY,
    CHOICE_ONCE,
    Capability,
    CallFlags,
    ConsentRequest,
    VOICE_VOCAB,
    VoiceConsent,
    label_for,
)
from cerebral.security.voice_consent import _map_to_choice


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTTS:
    """Records every speak() call. ``ready`` is constructor-configurable."""

    def __init__(self, *, ready: bool = True, raise_on_speak: bool = False) -> None:
        self.ready = ready
        self.raise_on_speak = raise_on_speak
        self.spoken: list[tuple[str, str | None]] = []

    async def speak(self, text: str, voice_id: str | None = None) -> None:
        self.spoken.append((text, voice_id))
        if self.raise_on_speak:
            raise RuntimeError("tts boom")


class _FakeAudioPipeline:
    """Records listener registrations and lets tests inject chunks."""

    def __init__(self) -> None:
        self.listeners: list = []
        # Counts of register / unregister, regardless of dedup
        self.register_calls = 0
        self.unregister_calls = 0

    def register_listener(self, listener) -> None:
        self.register_calls += 1
        if listener not in self.listeners:
            self.listeners.append(listener)

    def unregister_listener(self, listener) -> None:
        self.unregister_calls += 1
        try:
            self.listeners.remove(listener)
        except ValueError:
            pass

    def push(self, chunk: np.ndarray) -> None:
        """Drive every currently-registered listener (mirrors _audio_callback)."""
        for listener in tuple(self.listeners):
            listener(chunk)


class _ScriptedRecognizer:
    """Returns scripted strings as Vosk-like accept() outputs.

    On each call, returns the next scripted value (or None if exhausted).
    Used to script "yes"/"no"/"later"/[unk]/raise scenarios.
    """

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls = 0

    def accept(self, chunk_bytes: bytes) -> str | None:
        self.calls += 1
        if not self.script:
            return None
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def final(self) -> str:
        return ""


def _make_request(
    *,
    tool_name: str = "files.delete",
    capability: Capability = Capability.FS_DELETE,
    flags: CallFlags | None = None,
) -> ConsentRequest:
    return ConsentRequest(
        request_id="req-voice-1",
        tool_name=tool_name,
        capability=capability,
        flags=flags or CallFlags(),
        args_preview={"path": "/x"},
    )


# ---------------------------------------------------------------------------
# Slice 1 — choice mapping
# ---------------------------------------------------------------------------


def test_map_yes_to_once():
    assert _map_to_choice("yes") == CHOICE_ONCE
    assert _map_to_choice("YES") == CHOICE_ONCE
    assert _map_to_choice("  yes  ") == CHOICE_ONCE


def test_map_no_to_deny():
    assert _map_to_choice("no") == CHOICE_DENY


def test_map_later_to_deny():
    # "later" is functionally identical to "no" in v1 (sharpener #4).
    assert _map_to_choice("later") == CHOICE_DENY


def test_map_unknown_and_unk_to_deny():
    assert _map_to_choice("[unk]") == CHOICE_DENY
    assert _map_to_choice("maybe") == CHOICE_DENY
    assert _map_to_choice("") == CHOICE_DENY
    assert _map_to_choice(None) == CHOICE_DENY


def test_voice_vocab_is_closed_set():
    assert VOICE_VOCAB == ("yes", "no", "later")


# ---------------------------------------------------------------------------
# Slice 2 — ready property
# ---------------------------------------------------------------------------


def test_ready_true_when_tts_ready_and_factory_injected():
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True),
        audio_pipeline=_FakeAudioPipeline(),
        recognizer_factory=lambda: _ScriptedRecognizer(),
    )
    assert vc.ready is True


def test_ready_false_when_tts_not_ready():
    vc = VoiceConsent(
        tts=_FakeTTS(ready=False),
        audio_pipeline=_FakeAudioPipeline(),
        recognizer_factory=lambda: _ScriptedRecognizer(),
    )
    assert vc.ready is False


def test_ready_false_when_audio_pipeline_none():
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True),
        audio_pipeline=None,
        recognizer_factory=lambda: _ScriptedRecognizer(),
    )
    assert vc.ready is False


def test_ready_false_when_no_factory_and_pipeline_has_no_model():
    pipeline = _FakeAudioPipeline()
    # No vosk_model attribute → getattr returns None → not ready
    vc = VoiceConsent(tts=_FakeTTS(ready=True), audio_pipeline=pipeline)
    assert vc.ready is False


# ---------------------------------------------------------------------------
# Slice 3 — gist template (sharpener #2)
# ---------------------------------------------------------------------------


def test_gist_uses_plugin_name_when_available():
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True),
        audio_pipeline=_FakeAudioPipeline(),
        recognizer_factory=lambda: _ScriptedRecognizer(),
        plugin_name_for_tool=lambda _t: "Files",
    )
    req = _make_request(tool_name="files.delete", capability=Capability.FS_DELETE)
    gist = vc.build_gist(req)
    assert gist == f"Files wants to {label_for(Capability.FS_DELETE).lower()}. Yes, no, or later?"


def test_gist_falls_back_to_tool_name_when_plugin_unknown():
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True),
        audio_pipeline=_FakeAudioPipeline(),
        recognizer_factory=lambda: _ScriptedRecognizer(),
        plugin_name_for_tool=lambda _t: None,
    )
    req = _make_request(tool_name="files.delete", capability=Capability.FS_DELETE)
    gist = vc.build_gist(req)
    assert gist.startswith("files.delete wants to ")


def test_gist_omits_args_preview():
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True),
        audio_pipeline=_FakeAudioPipeline(),
        recognizer_factory=lambda: _ScriptedRecognizer(),
    )
    req = _make_request()
    gist = vc.build_gist(req)
    # Args are deliberately not in the spoken text (sharpener #2 — too
    # noisy; tray prompt carries the full preview).
    assert "/x" not in gist
    assert "path" not in gist


# ---------------------------------------------------------------------------
# Slice 4 — happy path: "yes" → CHOICE_ONCE
# ---------------------------------------------------------------------------


async def _drive_chunk_after_speak(pipeline: _FakeAudioPipeline, delay: float = 0.0) -> None:
    """Schedule a single chunk push after `delay` seconds so the recogniser
    can fire mid-await (post-speak)."""
    if delay:
        await asyncio.sleep(delay)
    chunk = np.zeros(4_000, dtype=np.int16)
    pipeline.push(chunk)


@pytest.mark.asyncio
async def test_prompt_yes_returns_once_and_unregisters_listener():
    tts = _FakeTTS(ready=True)
    pipeline = _FakeAudioPipeline()
    rec = _ScriptedRecognizer("yes")
    vc = VoiceConsent(
        tts=tts, audio_pipeline=pipeline,
        recognizer_factory=lambda: rec,
        voice_id_fn=lambda: "af_heart",
    )
    req = _make_request()

    async def drive():
        # Wait for the listener to register, then push a chunk
        for _ in range(20):
            if pipeline.listeners:
                break
            await asyncio.sleep(0.005)
        chunk = np.zeros(4_000, dtype=np.int16)
        pipeline.push(chunk)

    drive_task = asyncio.create_task(drive())
    choice = await vc.prompt(req)
    await drive_task

    assert choice == CHOICE_ONCE
    assert pipeline.register_calls == 1
    assert pipeline.unregister_calls == 1
    assert pipeline.listeners == []  # cleaned up
    # The active profile's voice_id was forwarded to TTS.
    assert tts.spoken == [(vc.build_gist(req), "af_heart")]


@pytest.mark.asyncio
async def test_prompt_no_returns_deny():
    pipeline = _FakeAudioPipeline()
    rec = _ScriptedRecognizer("no")
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True), audio_pipeline=pipeline,
        recognizer_factory=lambda: rec,
    )

    async def drive():
        for _ in range(20):
            if pipeline.listeners:
                break
            await asyncio.sleep(0.005)
        pipeline.push(np.zeros(4_000, dtype=np.int16))

    drive_task = asyncio.create_task(drive())
    choice = await vc.prompt(_make_request())
    await drive_task
    assert choice == CHOICE_DENY
    assert pipeline.listeners == []


@pytest.mark.asyncio
async def test_prompt_later_returns_deny():
    pipeline = _FakeAudioPipeline()
    rec = _ScriptedRecognizer("later")
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True), audio_pipeline=pipeline,
        recognizer_factory=lambda: rec,
    )

    async def drive():
        for _ in range(20):
            if pipeline.listeners:
                break
            await asyncio.sleep(0.005)
        pipeline.push(np.zeros(4_000, dtype=np.int16))

    drive_task = asyncio.create_task(drive())
    choice = await vc.prompt(_make_request())
    await drive_task
    assert choice == CHOICE_DENY


# ---------------------------------------------------------------------------
# Slice 5 — fail-closed paths (not ready / no recogniser / timeout / [unk])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_when_not_ready_denies_without_speaking():
    tts = _FakeTTS(ready=False)
    pipeline = _FakeAudioPipeline()
    vc = VoiceConsent(
        tts=tts, audio_pipeline=pipeline,
        recognizer_factory=lambda: _ScriptedRecognizer(),
    )
    assert vc.ready is False
    choice = await vc.prompt(_make_request())
    assert choice == CHOICE_DENY
    # Never registered a listener; never spoke.
    assert pipeline.register_calls == 0
    assert tts.spoken == []


@pytest.mark.asyncio
async def test_prompt_when_factory_returns_none_denies():
    pipeline = _FakeAudioPipeline()
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True),
        audio_pipeline=pipeline,
        recognizer_factory=lambda: None,  # factory yields no recogniser
    )
    # ``ready`` is True (constructor doesn't pre-build), but the prompt
    # fails closed when the factory returns None at call time.
    assert vc.ready is True
    choice = await vc.prompt(_make_request())
    assert choice == CHOICE_DENY
    assert pipeline.register_calls == 0


@pytest.mark.asyncio
async def test_prompt_timeout_returns_deny_and_unregisters_listener(monkeypatch):
    pipeline = _FakeAudioPipeline()
    rec = _ScriptedRecognizer()  # never returns a word
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True), audio_pipeline=pipeline,
        recognizer_factory=lambda: rec,
        max_listen_seconds=0.05,
    )
    choice = await vc.prompt(_make_request())
    assert choice == CHOICE_DENY
    assert pipeline.listeners == []  # unregistered
    assert pipeline.register_calls == 1
    assert pipeline.unregister_calls == 1


@pytest.mark.asyncio
async def test_prompt_when_tts_raises_returns_deny_and_unregisters(monkeypatch):
    pipeline = _FakeAudioPipeline()
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True, raise_on_speak=True),
        audio_pipeline=pipeline,
        recognizer_factory=lambda: _ScriptedRecognizer(),
    )
    choice = await vc.prompt(_make_request())
    assert choice == CHOICE_DENY
    # Listener was registered (try) and unregistered (finally), even though
    # tts.speak raised.
    assert pipeline.register_calls == 1
    assert pipeline.unregister_calls == 1
    assert pipeline.listeners == []


@pytest.mark.asyncio
async def test_prompt_recognizer_error_returns_deny():
    pipeline = _FakeAudioPipeline()
    rec = _ScriptedRecognizer(RuntimeError("vosk boom"))
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True), audio_pipeline=pipeline,
        recognizer_factory=lambda: rec,
        max_listen_seconds=0.2,
    )

    async def drive():
        for _ in range(20):
            if pipeline.listeners:
                break
            await asyncio.sleep(0.005)
        pipeline.push(np.zeros(4_000, dtype=np.int16))

    drive_task = asyncio.create_task(drive())
    choice = await vc.prompt(_make_request())
    await drive_task
    assert choice == CHOICE_DENY
    assert pipeline.listeners == []


# ---------------------------------------------------------------------------
# Slice 6 — listener fan-out idempotency + cleanup on cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_cancellation_still_unregisters_listener():
    pipeline = _FakeAudioPipeline()
    rec = _ScriptedRecognizer()  # never resolves
    vc = VoiceConsent(
        tts=_FakeTTS(ready=True), audio_pipeline=pipeline,
        recognizer_factory=lambda: rec,
        max_listen_seconds=5.0,  # long timeout — cancel before this fires
    )
    task = asyncio.create_task(vc.prompt(_make_request()))
    # Let the listener register and TTS finish.
    for _ in range(50):
        if pipeline.listeners:
            break
        await asyncio.sleep(0.005)
    assert pipeline.listeners != []
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # The finally block ran even though the task was cancelled.
    assert pipeline.listeners == []
    assert pipeline.unregister_calls >= 1


@pytest.mark.asyncio
async def test_prompt_only_one_recognizer_per_call():
    # Two sequential prompts must not leak listeners or share recogniser
    # state. Each prompt() opens its own recogniser via the factory.
    pipeline = _FakeAudioPipeline()
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return _ScriptedRecognizer("yes")

    vc = VoiceConsent(
        tts=_FakeTTS(ready=True), audio_pipeline=pipeline,
        recognizer_factory=factory,
    )

    async def drive():
        for _ in range(20):
            if pipeline.listeners:
                break
            await asyncio.sleep(0.005)
        pipeline.push(np.zeros(4_000, dtype=np.int16))

    for _ in range(2):
        drive_task = asyncio.create_task(drive())
        choice = await vc.prompt(_make_request())
        await drive_task
        assert choice == CHOICE_ONCE
        assert pipeline.listeners == []

    assert factory_calls == 2  # one recogniser per call
