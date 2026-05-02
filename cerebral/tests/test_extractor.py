"""
5W1H extractor tests — Issue #11.

Unit tests inject a fake ModelRouter (AsyncMock); no LLM or network required.
"""
import json
import pytest
from unittest.mock import AsyncMock

from cerebral.passive.extractor import CandidateAction, FiveW1HExtractor
from cerebral.llm.router import ModelRouter


# ── helpers ────────────────────────────────────────────────────────────────────

def _router(response: str) -> ModelRouter:
    backend = AsyncMock()
    backend.complete.return_value = response
    return ModelRouter(backends={"ollama/gemma4": backend}, default_model="ollama/gemma4")


def _valid_llm_response(**overrides) -> str:
    payload = {
        "title": "Call the dentist",
        "summary": "Felix detected: you need to call the dentist to reschedule your appointment",
        "fields": {
            "who": "dentist",
            "what": "call to reschedule appointment",
            "when": "today",
            "where": "",
            "why": "reschedule missed appointment",
            "how": "phone call",
        },
        "confidence": 0.85,
    }
    payload.update(overrides)
    return json.dumps(payload)


CLEAR_TRANSCRIPT = "I need to call the dentist today to reschedule my appointment"


# ── Slice 1: extract() returns CandidateAction for a clear transcript ──────────

async def test_extract_returns_candidate_action():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert isinstance(result, CandidateAction)


async def test_candidate_action_has_title():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result.title == "Call the dentist"


async def test_candidate_action_has_summary():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert "dentist" in result.summary


async def test_candidate_action_has_confidence():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result.confidence == pytest.approx(0.85)


# ── Slice 2: fields dict contains all 5W1H keys ───────────────────────────────

async def test_candidate_action_fields_has_all_5w1h_keys():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    for key in ("who", "what", "when", "where", "why", "how"):
        assert key in result.fields, f"missing field: {key}"


async def test_candidate_action_fields_values_are_strings():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    for key, val in result.fields.items():
        assert isinstance(val, str), f"field '{key}' is not a string"


# ── Slice 3: LLM prompt includes the transcript ───────────────────────────────

async def test_prompt_contains_transcript():
    backend = AsyncMock()
    backend.complete.return_value = _valid_llm_response()
    router = ModelRouter(backends={"ollama/gemma4": backend}, default_model="ollama/gemma4")
    ext = FiveW1HExtractor(router)

    await ext.extract(CLEAR_TRANSCRIPT)

    call_args = backend.complete.call_args
    prompt = call_args[0][0]
    assert CLEAR_TRANSCRIPT in prompt


# ── Slice 4: confidence < 0.5 → returns None ─────────────────────────────────

async def test_low_confidence_returns_none():
    ext = FiveW1HExtractor(_router(_valid_llm_response(confidence=0.3)))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is None


async def test_confidence_exactly_at_threshold_returns_action():
    ext = FiveW1HExtractor(_router(_valid_llm_response(confidence=0.5)))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is not None


async def test_confidence_just_below_threshold_returns_none():
    ext = FiveW1HExtractor(_router(_valid_llm_response(confidence=0.49)))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is None


# ── Slice 5: empty / very short transcript → None, no LLM call ───────────────

async def test_empty_transcript_returns_none():
    backend = AsyncMock()
    backend.complete.return_value = _valid_llm_response()
    router = ModelRouter(backends={"ollama/gemma4": backend}, default_model="ollama/gemma4")
    ext = FiveW1HExtractor(router)

    result = await ext.extract("")
    assert result is None


async def test_empty_transcript_does_not_call_llm():
    backend = AsyncMock()
    backend.complete.return_value = _valid_llm_response()
    router = ModelRouter(backends={"ollama/gemma4": backend}, default_model="ollama/gemma4")
    ext = FiveW1HExtractor(router)

    await ext.extract("")
    backend.complete.assert_not_called()


async def test_whitespace_only_transcript_returns_none():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract("   \n  ")
    assert result is None


# ── Slice 6: unparseable LLM response → None ─────────────────────────────────

async def test_garbled_llm_response_returns_none():
    ext = FiveW1HExtractor(_router("Sorry, I can't help with that."))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is None


async def test_partial_json_returns_none():
    ext = FiveW1HExtractor(_router('{"title": "incomplete"'))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is None


async def test_json_missing_required_fields_returns_none():
    ext = FiveW1HExtractor(_router('{"title": "no confidence field"}'))
    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is None


# ── Slice 7: LLM raises → None ───────────────────────────────────────────────

async def test_llm_error_returns_none():
    backend = AsyncMock()
    backend.complete.side_effect = Exception("backend exploded")
    router = ModelRouter(backends={"ollama/gemma4": backend}, default_model="ollama/gemma4")
    ext = FiveW1HExtractor(router)

    result = await ext.extract(CLEAR_TRANSCRIPT)
    assert result is None
