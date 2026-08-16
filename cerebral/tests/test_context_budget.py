"""Tests for context budget — token estimation & threshold detection."""

import os
import pytest
from unittest.mock import AsyncMock

from cerebral.llm.router import ModelRouter, CLOUD_MODELS
from cerebral.llm.context_budget import (
    estimate_tokens,
    is_over_threshold,
    COMPACTION_THRESHOLD,
)


# ── estimate_tokens ──────────────────────────────────────────────────────────

def test_estimate_tokens_empty_returns_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_known_length():
    text = "a" * 100
    assert estimate_tokens(text) == 100 // 4


def test_estimate_tokens_non_string_returns_zero():
    assert estimate_tokens(None) == 0
    assert estimate_tokens(123) == 0
    assert estimate_tokens([]) == 0


# ── is_over_threshold ────────────────────────────────────────────────────────

def test_is_over_threshold_returns_true_when_above():
    # 30000 chars -> 7500 tokens. 0.70 * 8192 = 5734.4. 7500 > 5734.4
    long_text = "x" * 30000
    assert is_over_threshold(long_text, 8192) is True


def test_is_over_threshold_returns_false_when_below():
    short_text = "x" * 1000  # 250 tokens
    assert is_over_threshold(short_text, 8192) is False


def test_is_over_threshold_handles_zero_window():
    assert is_over_threshold("anything", 0) is False
    assert is_over_threshold("anything", -1) is False


def test_is_over_threshold_respects_custom_threshold():
    # 1000 chars -> 250 tokens. window=1000, threshold=0.2 -> limit=200. 250 > 200
    assert is_over_threshold("x" * 1000, 1000, threshold=0.2) is True


# ── context_window_for via ModelRouter ────────────────────────────────────────

def test_context_window_for_returns_wired_value():
    ollama = AsyncMock()
    claw = AsyncMock()
    router = ModelRouter(
        backends={"ollama/qwen3:8b": ollama, "claude/sonnet": claw},
        models={
            "ollama/qwen3:8b": {"label": "Qwen", "is_cloud": False},
            "claude/sonnet": {"label": "Sonnet", "is_cloud": True, "context_window": 200000},
        },
    )
    assert router.context_window_for("claude/sonnet") == 200000


def test_context_window_for_returns_default_when_missing():
    router = ModelRouter(backends={"ollama/qwen3:8b": AsyncMock()})
    assert router.context_window_for("ollama/qwen3:8b") == 8192
    assert router.context_window_for("unknown/model") == 8192


def test_cloud_models_carry_context_window():
    assert CLOUD_MODELS["claude/sonnet"]["context_window"] == 200000
    assert CLOUD_MODELS["claude/haiku"]["context_window"] == 200000
