"""Token estimation, threshold detection, and model context_window metadata
(harness parity H1-S1 / #732). Hermetic -- fake backends, no model/net."""

from unittest.mock import AsyncMock

from cerebral.llm.router import ModelRouter, CLOUD_MODELS, _real_models
from cerebral.llm.context_budget import estimate_tokens, is_over_threshold


# -- estimate_tokens ----------------------------------------------------------

def test_estimate_tokens_empty_returns_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_known_length():
    assert estimate_tokens("a" * 100) == 100 // 4


def test_estimate_tokens_non_string_returns_zero():
    assert estimate_tokens(None) == 0
    assert estimate_tokens(123) == 0
    assert estimate_tokens([]) == 0


# -- is_over_threshold --------------------------------------------------------

def test_is_over_threshold_true_when_above():
    # 30000 chars -> 7500 tokens; 0.70 * 8192 = 5734.4; 7500 > 5734.4
    assert is_over_threshold("x" * 30000, 8192) is True


def test_is_over_threshold_false_when_below():
    assert is_over_threshold("x" * 1000, 8192) is False  # 250 tokens


def test_is_over_threshold_handles_zero_or_negative_window():
    assert is_over_threshold("anything", 0) is False
    assert is_over_threshold("anything", -1) is False


def test_is_over_threshold_respects_custom_threshold():
    # 1000 chars -> 250 tokens; window 1000 * 0.2 = 200; 250 > 200
    assert is_over_threshold("x" * 1000, 1000, threshold=0.2) is True


# -- context_window_for via ModelRouter ---------------------------------------

def test_context_window_for_returns_wired_value():
    router = ModelRouter(
        backends={"ollama/qwen3:8b": AsyncMock(), "claude/sonnet": AsyncMock()},
        models={
            "ollama/qwen3:8b": {"label": "Qwen", "is_cloud": False},
            "claude/sonnet": {"label": "Sonnet", "is_cloud": True, "context_window": 200000},
        },
    )
    assert router.context_window_for("claude/sonnet") == 200000


def test_context_window_for_returns_default_when_missing():
    router = ModelRouter(backends={"ollama/qwen3:8b": AsyncMock()})
    assert router.context_window_for("ollama/qwen3:8b") == 8192   # no metadata -> floor
    assert router.context_window_for("unknown/model") == 8192     # unknown -> floor


def test_cloud_models_carry_context_window():
    assert CLOUD_MODELS["claude/sonnet"]["context_window"] == 200000
    assert CLOUD_MODELS["claude/haiku"]["context_window"] == 200000


def test_real_models_wires_context_window_into_metadata():
    # The runtime metadata builder must carry context_window through, else
    # context_window_for falls back to the floor for real cloud backends.
    models = _real_models({"claude/sonnet": AsyncMock()})
    assert models["claude/sonnet"]["context_window"] == 200000
