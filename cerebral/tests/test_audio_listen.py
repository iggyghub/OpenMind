"""Endpointing + verbal listen-mode logic (feat/voice-endpointing).

Pure-function tests — no real mic, no Whisper, no threads. Covers the risky
decision logic: when an utterance ends, and how a wake command maps to a
listen mode.
"""
import math

import numpy as np

from cerebral.audio.pipeline import (
    CHUNK_SECONDS,
    endpoint_reached,
    is_stop_phrase,
    parse_listen_directive,
    _rms,
)

LOUD = 4000.0    # well above SILENCE_RMS (500)
QUIET = 50.0     # well below


def _series(pattern: str) -> list[float]:
    """'.' = silence chunk, '#' = speech chunk -> RMS series."""
    return [LOUD if c == "#" else QUIET for c in pattern]


# ── endpoint_reached ─────────────────────────────────────────────────────────

def test_no_speech_times_out():
    # All silence: ends at NO_SPEECH_TIMEOUT_S (4s / 0.25 = 16 chunks).
    ended, keep = endpoint_reached(_series("." * 20))
    assert ended
    assert keep == int(4.0 / CHUNK_SECONDS)


def test_speech_then_silence_endpoints_after_hangover():
    # 8 speech chunks (2s), then silence. Hangover 1.2s needs ceil(1.2/0.25)=5
    # quiet chunks (4 chunks = 1.0s < 1.2s), so it ends at chunk 13.
    ended, keep = endpoint_reached(_series("########" + "." * 10))
    assert ended
    assert keep == 8 + math.ceil(1.2 / CHUNK_SECONDS)  # 8 + 5 = 13


def test_still_talking_not_ended():
    # Continuous speech, no trailing silence yet, under the max cap.
    ended, keep = endpoint_reached(_series("#" * 10))
    assert not ended
    assert keep == 10


def test_max_utterance_cap_forces_end():
    # 15s cap / 0.25 = 60 chunks of unbroken speech -> forced endpoint.
    ended, keep = endpoint_reached(_series("#" * 80))
    assert ended
    assert keep == int(15.0 / CHUNK_SECONDS)


def test_brief_pause_does_not_endpoint():
    # A short 0.5s gap (< 1.2s hangover) mid-sentence must NOT end it.
    ended, _ = endpoint_reached(_series("#####" + ".." + "#####"))
    assert not ended


# ── parse_listen_directive ───────────────────────────────────────────────────

def test_single_command_is_default():
    assert parse_listen_directive("what's the weather today") == ("single", 0.0)


def test_listen_for_seconds():
    assert parse_listen_directive("Felix listen for 30 seconds") == ("timed", 30.0)


def test_listen_for_minutes():
    assert parse_listen_directive("listen for 2 minutes please") == ("timed", 120.0)


def test_keep_listening_is_continuous():
    assert parse_listen_directive(
        "keep listening until I say felix stop"
    ) == ("continuous", 0.0)


# ── is_stop_phrase ───────────────────────────────────────────────────────────

def test_stop_phrases():
    assert is_stop_phrase("Felix stop")
    assert is_stop_phrase("stop")
    assert is_stop_phrase("stop listening.")


def test_non_stop_command_passes_through():
    # A normal command that merely contains "stop" must not end the session.
    assert not is_stop_phrase("stop the kitchen timer")


# ── _rms sanity ──────────────────────────────────────────────────────────────

def test_rms_silence_below_threshold_speech_above():
    silence = np.zeros(4000, dtype=np.int16)
    speech = (np.ones(4000) * 4000).astype(np.int16)
    assert _rms(silence) < 500.0
    assert _rms(speech) > 500.0
    assert _rms(np.array([], dtype=np.int16)) == 0.0
