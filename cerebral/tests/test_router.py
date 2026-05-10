"""
Model router tests — Issue #6.

Unit tests use injected fake backends (no HTTP). Integration tests (@pytest.mark.integration)
hit real services and are skipped unless explicitly selected.
"""
import logging
import pytest
from unittest.mock import AsyncMock
import logging as _logging

from cerebral.llm.router import ModelRouter, ModelUnavailableError


# ---------------------------------------------------------------------------
# Slice 1 — complete() delegates to the active backend
# ---------------------------------------------------------------------------

async def test_complete_returns_response_from_default_backend():
    backend = AsyncMock()
    backend.complete.return_value = "Tokyo"
    router = ModelRouter(backends={"ollama/gemma4": backend})
    result = await router.complete("What is the capital of Japan?")
    assert result == "Tokyo"


async def test_complete_passes_prompt_to_backend():
    backend = AsyncMock()
    backend.complete.return_value = "ok"
    router = ModelRouter(backends={"ollama/gemma4": backend})
    await router.complete("hello", task_type="chat")
    backend.complete.assert_called_once_with("hello", "chat")


# ---------------------------------------------------------------------------
# Slice 2 — switch_model() routes to a different backend
# ---------------------------------------------------------------------------

async def test_switch_model_routes_to_new_backend():
    ollama = AsyncMock(); ollama.complete.return_value = "from ollama"
    claw = AsyncMock(); claw.complete.return_value = "from claude"
    router = ModelRouter(backends={"ollama/gemma4": ollama, "claude/haiku": claw})

    router.switch_model("claude/haiku")
    result = await router.complete("ping")

    assert result == "from claude"
    ollama.complete.assert_not_called()


def test_active_model_reflects_switch():
    ollama = AsyncMock()
    claw = AsyncMock()
    router = ModelRouter(backends={"ollama/gemma4": ollama, "claude/haiku": claw})
    assert router.active_model == "ollama/gemma4"
    router.switch_model("claude/haiku")
    assert router.active_model == "claude/haiku"


# ---------------------------------------------------------------------------
# Slice 3 — backend failure → ModelUnavailableError, no silent cloud fallback
# ---------------------------------------------------------------------------

async def test_offline_backend_raises_model_unavailable():
    offline = AsyncMock()
    offline.complete.side_effect = ConnectionError("Ollama not running")
    router = ModelRouter(backends={"ollama/gemma4": offline})
    with pytest.raises(ModelUnavailableError, match="ollama/gemma4"):
        await router.complete("ping")


async def test_offline_backend_does_not_call_cloud_backend():
    offline = AsyncMock()
    offline.complete.side_effect = ConnectionError("Ollama not running")
    cloud = AsyncMock()
    cloud.complete.return_value = "sneaky cloud response"
    router = ModelRouter(backends={"ollama/gemma4": offline, "claude/haiku": cloud})

    with pytest.raises(ModelUnavailableError):
        await router.complete("ping")

    cloud.complete.assert_not_called()


# ---------------------------------------------------------------------------
# Slice 4 — complete() logs which model handled the request
# ---------------------------------------------------------------------------

async def test_complete_logs_active_model(caplog):
    backend = AsyncMock()
    backend.complete.return_value = "answer"
    router = ModelRouter(backends={"ollama/gemma4": backend})

    with caplog.at_level(_logging.INFO, logger="cerebral.llm.router"):
        await router.complete("question")

    assert "ollama/gemma4" in caplog.text


async def test_complete_logs_correct_model_after_switch(caplog):
    ollama = AsyncMock(); ollama.complete.return_value = "a"
    claw = AsyncMock(); claw.complete.return_value = "b"
    router = ModelRouter(backends={"ollama/gemma4": ollama, "claude/haiku": claw})
    router.switch_model("claude/haiku")

    with caplog.at_level(_logging.INFO, logger="cerebral.llm.router"):
        await router.complete("question")

    assert "claude/haiku" in caplog.text


# ---------------------------------------------------------------------------
# Slice 5 — switch_model() to unknown model raises ValueError
# ---------------------------------------------------------------------------

def test_switch_to_unknown_model_raises():
    router = ModelRouter(backends={"ollama/gemma4": AsyncMock()})
    with pytest.raises(ValueError, match="unknown model"):
        router.switch_model("gpt-5/magic")


def test_active_model_unchanged_after_failed_switch():
    router = ModelRouter(backends={"ollama/gemma4": AsyncMock()})
    with pytest.raises(ValueError):
        router.switch_model("gpt-5/magic")
    assert router.active_model == "ollama/gemma4"


# ---------------------------------------------------------------------------
# Slice 6 — list_models, last_model, cloud flag, per-task mapping (Issue #29)
# ---------------------------------------------------------------------------


def _two_model_router():
    ollama = AsyncMock(); ollama.complete = AsyncMock(return_value="from ollama")
    claw = AsyncMock(); claw.complete = AsyncMock(return_value="from claude")
    return ModelRouter(
        backends={"ollama/gemma4": ollama, "claude/haiku": claw},
        models={
            "ollama/gemma4": {"label": "Gemma 4 (local)", "is_cloud": False},
            "claude/haiku": {"label": "Claude Haiku", "is_cloud": True},
        },
    ), ollama, claw


def test_list_models_returns_metadata_for_each_backend():
    router, _, _ = _two_model_router()
    models = router.list_models()
    ids = [m["id"] for m in models]
    assert "ollama/gemma4" in ids and "claude/haiku" in ids
    haiku = next(m for m in models if m["id"] == "claude/haiku")
    assert haiku["label"] == "Claude Haiku"
    assert haiku["is_cloud"] is True


def test_list_models_marks_active_and_last():
    router, _, _ = _two_model_router()
    models = router.list_models()
    assert next(m for m in models if m["id"] == "ollama/gemma4")["is_active"] is True
    # No requests yet, so no last
    assert all(m["is_last"] is False for m in models)


async def test_last_model_tracks_who_handled_request():
    router, _, _ = _two_model_router()
    assert router.last_model is None
    await router.complete("ping")
    assert router.last_model == "ollama/gemma4"
    router.switch_model("claude/haiku")
    await router.complete("ping")
    assert router.last_model == "claude/haiku"


async def test_last_model_unchanged_on_failure():
    offline = AsyncMock(); offline.complete.side_effect = ConnectionError("no")
    router = ModelRouter(backends={"ollama/gemma4": offline})
    with pytest.raises(ModelUnavailableError):
        await router.complete("ping")
    assert router.last_model is None


def test_active_is_cloud_reflects_model():
    router, _, _ = _two_model_router()
    assert router.active_is_cloud is False
    router.switch_model("claude/haiku")
    assert router.active_is_cloud is True


def test_models_metadata_defaults_when_not_supplied():
    router = ModelRouter(backends={"ollama/gemma4": AsyncMock()})
    models = router.list_models()
    assert len(models) == 1
    assert models[0]["is_cloud"] is False
    assert models[0]["label"] == "ollama/gemma4"


# Per-task-type mapping ------------------------------------------------------

def test_set_task_model_pins_model_for_task():
    router, _, _ = _two_model_router()
    router.set_task_model("extraction", "claude/haiku")
    assert router.get_task_model("extraction") == "claude/haiku"
    # Other tasks fall back to active
    assert router.get_task_model("chat") == "ollama/gemma4"


def test_set_task_model_with_none_clears_mapping():
    router, _, _ = _two_model_router()
    router.set_task_model("extraction", "claude/haiku")
    router.set_task_model("extraction", None)
    assert router.get_task_model("extraction") == "ollama/gemma4"
    assert "extraction" not in router.task_models()


def test_set_task_model_unknown_model_raises():
    router, _, _ = _two_model_router()
    with pytest.raises(ValueError, match="unknown model"):
        router.set_task_model("chat", "gpt-5/magic")


async def test_complete_uses_task_specific_model():
    router, ollama, claw = _two_model_router()
    router.set_task_model("extraction", "claude/haiku")
    result = await router.complete("classify this", task_type="extraction")
    assert result == "from claude"
    ollama.complete.assert_not_called()
    claw.complete.assert_called_once_with("classify this", "extraction")


async def test_complete_falls_back_to_active_when_no_task_mapping():
    router, ollama, claw = _two_model_router()
    # No mapping set — chat should go to active (ollama)
    result = await router.complete("hi", task_type="chat")
    assert result == "from ollama"
    claw.complete.assert_not_called()


async def test_per_task_mapping_persists_after_switch_model():
    router, ollama, claw = _two_model_router()
    router.set_task_model("extraction", "claude/haiku")
    router.switch_model("claude/haiku")
    # active is now claude; extraction still pinned to claude (same)
    # but a non-mapped task uses active
    await router.complete("hi", task_type="chat")
    claw.complete.assert_called_with("hi", "chat")
    assert router.get_task_model("extraction") == "claude/haiku"


def test_task_models_returns_copy():
    router, _, _ = _two_model_router()
    router.set_task_model("chat", "claude/haiku")
    snapshot = router.task_models()
    snapshot["chat"] = "tampered"
    assert router.get_task_model("chat") == "claude/haiku"


# ---------------------------------------------------------------------------
# Slice 7 — integration tests (real HTTP; skipped unless -m integration)
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_ollama_backend_real_call():
    """OllamaBackend hits live Ollama at localhost:11434."""
    from cerebral.llm.router import OllamaBackend
    backend = OllamaBackend(url="http://localhost:11434", model="gemma4")
    result = await backend.complete("Reply with only the word PONG.", "chat")
    assert isinstance(result, str)
    assert len(result.strip()) > 0


@pytest.mark.integration
async def test_model_router_end_to_end_ollama():
    """ModelRouter with no injection calls real Ollama by default."""
    router = ModelRouter()
    result = await router.complete("Reply with only the word PONG.", "chat")
    assert isinstance(result, str)
    assert len(result.strip()) > 0
    assert router.active_model == "ollama/gemma4"


@pytest.mark.integration
async def test_claw_backend_real_call():
    """ClawBackend hits live OpenClaw at localhost:3000 for Claude routing."""
    from cerebral.llm.router import ClawBackend
    backend = ClawBackend(url="http://localhost:3000")
    result = await backend.complete("Reply with only the word PONG.", "chat")
    assert isinstance(result, str)
    assert len(result.strip()) > 0
