"""
AudioPipeline signal-word / passive-mode tests — Issue #11.

Tests exercise the detection logic and dispatch methods directly, bypassing
the hardware (sounddevice/Vosk) layer — consistent with the existing pipeline
design (no sounddevice in test environments).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from cerebral.audio.pipeline import AudioPipeline


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_pipeline(on_passive=None, signal_words=None):
    on_wake = AsyncMock()
    if on_passive is None:
        on_passive = AsyncMock()
    return AudioPipeline(
        on_wake=on_wake,
        on_passive=on_passive,
        signal_words=signal_words or ["meeting", "remind", "call"],
    )


# ── Slice 8: on_passive called when signal word detected ─────────────────────

async def test_signal_word_triggers_on_passive():
    on_passive = AsyncMock()
    pipeline = _make_pipeline(on_passive=on_passive)

    transcript = "we have a meeting tomorrow at 3pm"
    with patch.object(pipeline, "_transcribe", return_value=transcript):
        await pipeline._on_signal_detected(transcript_hint=transcript)

    on_passive.assert_awaited_once_with(transcript)


async def test_on_passive_receives_transcribed_text():
    on_passive = AsyncMock()
    pipeline = _make_pipeline(on_passive=on_passive)

    transcript = "remind me to buy milk on the way home"
    with patch.object(pipeline, "_transcribe", return_value=transcript):
        await pipeline._on_signal_detected(transcript_hint=transcript)

    call_arg = on_passive.call_args[0][0]
    assert call_arg == transcript


# ── Slice 9: buffer cleared after passive transcription ──────────────────────

async def test_buffer_cleared_after_passive_transcription():
    pipeline = _make_pipeline()

    # Seed the buffer with some audio
    dummy_audio = np.zeros(1000, dtype=np.int16)
    pipeline._buffer.extend(dummy_audio)
    assert len(pipeline._buffer) > 0

    transcript = "call the dentist"
    with patch.object(pipeline, "_transcribe", return_value=transcript):
        await pipeline._on_signal_detected(transcript_hint=transcript)

    assert len(pipeline._buffer) == 0


# ── Slice 10: on_wake NOT called for signal word ──────────────────────────────

async def test_on_wake_not_called_for_signal_word():
    on_wake = AsyncMock()
    on_passive = AsyncMock()
    pipeline = AudioPipeline(
        on_wake=on_wake,
        on_passive=on_passive,
        signal_words=["meeting"],
    )

    transcript = "there is a meeting at noon"
    with patch.object(pipeline, "_transcribe", return_value=transcript):
        await pipeline._on_signal_detected(transcript_hint=transcript)

    on_wake.assert_not_called()


# ── Slice 11: no signal_words → on_passive never reachable ───────────────────

def test_pipeline_without_signal_words_has_empty_list():
    pipeline = AudioPipeline(on_wake=AsyncMock())
    assert pipeline.signal_words == []


def test_pipeline_stores_configured_signal_words():
    words = ["remind", "call", "meeting"]
    pipeline = AudioPipeline(on_wake=AsyncMock(), signal_words=words)
    assert pipeline.signal_words == words


# ── Slice 12: _matches_signal_word detects presence correctly ─────────────────

def test_matches_signal_word_returns_true_for_known_word():
    pipeline = _make_pipeline(signal_words=["meeting", "remind"])
    assert pipeline._matches_signal_word("we have a meeting tomorrow") is True


def test_matches_signal_word_returns_false_when_no_match():
    pipeline = _make_pipeline(signal_words=["meeting", "remind"])
    assert pipeline._matches_signal_word("the weather is nice today") is False


def test_matches_signal_word_is_case_insensitive():
    pipeline = _make_pipeline(signal_words=["remind"])
    assert pipeline._matches_signal_word("REMIND me to do this") is True


def test_matches_signal_word_empty_signal_words_returns_false():
    pipeline = AudioPipeline(on_wake=AsyncMock(), signal_words=[])
    assert pipeline._matches_signal_word("meeting at noon") is False


# ── Slice 13: on_passive=None → no error when signal triggers ────────────────

async def test_no_on_passive_callback_does_not_raise():
    pipeline = AudioPipeline(
        on_wake=AsyncMock(),
        on_passive=None,
        signal_words=["meeting"],
    )
    transcript = "there is a meeting"
    with patch.object(pipeline, "_transcribe", return_value=transcript):
        # Should complete without raising even with no on_passive handler
        await pipeline._on_signal_detected(transcript_hint=transcript)
