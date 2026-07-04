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
# Issue #349 — graceful fallback + default "quality" seeding
# ---------------------------------------------------------------------------


async def test_complete_falls_back_to_active_when_task_model_offline(caplog):
    active = AsyncMock(); active.complete.return_value = "from active"
    quality = AsyncMock(); quality.complete.side_effect = ConnectionError("gone")
    router = ModelRouter(backends={"ollama/qwen2.5:7b": active, "ollama/qwen3:8b": quality})
    router.set_task_model("quality", "ollama/qwen3:8b")

    with caplog.at_level(_logging.WARNING, logger="cerebral.llm.router"):
        result = await router.complete("map fields", task_type="quality")

    assert result == "from active"
    assert "falling back" in caplog.text
    assert router.last_model == "ollama/qwen2.5:7b"


async def test_complete_falls_back_when_task_mapping_points_at_missing_backend(caplog):
    active = AsyncMock(); active.complete.return_value = "ok"
    router = ModelRouter(backends={"ollama/qwen2.5:7b": active})
    # Stale mapping (set_task_model validates, so poke the dict directly —
    # mirrors a model uninstalled outside refresh_local_backends).
    router._task_models["quality"] = "ollama/uninstalled"

    with caplog.at_level(_logging.WARNING, logger="cerebral.llm.router"):
        assert await router.complete("hi", task_type="quality") == "ok"

    assert "not available" in caplog.text


async def test_complete_still_raises_when_active_also_offline():
    a = AsyncMock(); a.complete.side_effect = ConnectionError("no a")
    b = AsyncMock(); b.complete.side_effect = ConnectionError("no b")
    router = ModelRouter(backends={"ollama/a": a, "ollama/b": b})
    router.set_task_model("quality", "ollama/b")
    with pytest.raises(ModelUnavailableError, match="ollama/a"):
        await router.complete("hi", task_type="quality")


def test_seed_quality_default_prefers_local_qwen3():
    router = ModelRouter(backends={
        "ollama/qwen2.5:7b": AsyncMock(),
        "ollama/qwen3:8b": AsyncMock(),
        "claude/sonnet": AsyncMock(),
    })
    assert router.seed_quality_default() == "ollama/qwen3:8b"
    assert router.get_task_model("quality") == "ollama/qwen3:8b"


def test_seed_quality_default_falls_back_to_cloud():
    router = ModelRouter(backends={
        "ollama/qwen2.5:7b": AsyncMock(),
        "claude/sonnet": AsyncMock(),
    })
    assert router.seed_quality_default() == "claude/sonnet"
    assert router.get_task_model("quality") == "claude/sonnet"


def test_seed_quality_default_none_when_nothing_preferred():
    router = ModelRouter(backends={"ollama/qwen2.5:7b": AsyncMock()})
    assert router.seed_quality_default() is None
    # Unmapped — "quality" resolves to the active model.
    assert router.get_task_model("quality") == "ollama/qwen2.5:7b"


# ---------------------------------------------------------------------------
# Slice 7 — Ollama auto-discovery (Issue #37)
# ---------------------------------------------------------------------------


def test_list_installed_models_returns_names_from_tags_endpoint():
    from cerebral.llm.router import OllamaBackend
    fake_tags = lambda url: {"models": [
        {"name": "gemma3:latest"}, {"name": "llama3.2:3b"},
    ]}
    names = OllamaBackend.list_installed_models(tags_fetch_fn=fake_tags)
    assert names == ["gemma3:latest", "llama3.2:3b"]


def test_list_installed_models_returns_empty_when_ollama_offline(caplog):
    from cerebral.llm.router import OllamaBackend
    def offline_fetch(url):
        raise ConnectionError("Ollama not running")
    with caplog.at_level(_logging.WARNING, logger="cerebral.llm.router"):
        names = OllamaBackend.list_installed_models(tags_fetch_fn=offline_fetch)
    assert names == []
    assert "Ollama unreachable" in caplog.text


def test_list_installed_models_handles_empty_response():
    from cerebral.llm.router import OllamaBackend
    fake_tags = lambda url: {"models": []}
    assert OllamaBackend.list_installed_models(tags_fetch_fn=fake_tags) == []


def test_list_installed_models_handles_missing_models_key():
    from cerebral.llm.router import OllamaBackend
    fake_tags = lambda url: {}  # malformed
    assert OllamaBackend.list_installed_models(tags_fetch_fn=fake_tags) == []


# ---------------------------------------------------------------------------
# Slice 8 — Router auto-picks default from discovery (Issue #37)
# ---------------------------------------------------------------------------


def test_router_default_picks_first_ollama_model_when_none_specified():
    a = AsyncMock(); b = AsyncMock()
    router = ModelRouter(
        backends={"ollama/gemma3:latest": a, "ollama/llama3.2": b, "claude/haiku": AsyncMock()}
    )
    assert router.active_model == "ollama/gemma3:latest"


def test_router_default_falls_back_to_cloud_when_no_local_models():
    """If Ollama has no models, default to first cloud backend so chat still works."""
    cloud = AsyncMock()
    router = ModelRouter(backends={"claude/haiku": cloud})
    assert router.active_model == "claude/haiku"


def test_router_raises_when_no_backends_at_all():
    with pytest.raises(ValueError, match="no model backends"):
        ModelRouter(backends={})


def test_router_explicit_default_still_honored():
    a = AsyncMock(); b = AsyncMock()
    router = ModelRouter(
        backends={"ollama/a": a, "ollama/b": b},
        default_model="ollama/b",
    )
    assert router.active_model == "ollama/b"


# ---------------------------------------------------------------------------
# Slice 9 — refresh_local_backends() (Issue #37)
# ---------------------------------------------------------------------------


def test_refresh_local_backends_adds_newly_installed_models():
    cloud = AsyncMock()
    router = ModelRouter(backends={"claude/haiku": cloud})
    fake_tags = lambda url: {"models": [{"name": "gemma3:latest"}]}
    new_ids = router.refresh_local_backends(tags_fetch_fn=fake_tags)
    assert new_ids == ["ollama/gemma3:latest"]
    assert "ollama/gemma3:latest" in [m["id"] for m in router.list_models()]


def test_refresh_local_backends_drops_uninstalled_models():
    """If user runs `ollama rm gemma3` and refreshes, the model disappears from picker."""
    router = ModelRouter(
        backends={"ollama/gemma3:latest": AsyncMock(), "claude/haiku": AsyncMock()},
        models={"ollama/gemma3:latest": {"label": "Gemma 3", "is_cloud": False},
                "claude/haiku": {"label": "Haiku", "is_cloud": True}},
        default_model="claude/haiku",
    )
    fake_tags = lambda url: {"models": []}  # nothing installed
    router.refresh_local_backends(tags_fetch_fn=fake_tags)
    assert "ollama/gemma3:latest" not in [m["id"] for m in router.list_models()]
    # Cloud entries preserved
    assert "claude/haiku" in [m["id"] for m in router.list_models()]


def test_refresh_local_backends_reassigns_active_when_active_uninstalled():
    """If the active model was uninstalled, fall back to another available backend."""
    router = ModelRouter(
        backends={"ollama/gemma3:latest": AsyncMock(), "claude/haiku": AsyncMock()},
    )
    assert router.active_model == "ollama/gemma3:latest"
    fake_tags = lambda url: {"models": []}
    router.refresh_local_backends(tags_fetch_fn=fake_tags)
    # Active had to move off the uninstalled model
    assert router.active_model == "claude/haiku"


def test_refresh_local_backends_keeps_active_when_still_installed():
    cloud = AsyncMock()
    router = ModelRouter(
        backends={"ollama/gemma3:latest": AsyncMock(), "claude/haiku": cloud},
        default_model="claude/haiku",
    )
    fake_tags = lambda url: {"models": [{"name": "gemma3:latest"}]}
    router.refresh_local_backends(tags_fetch_fn=fake_tags)
    assert router.active_model == "claude/haiku"  # unchanged


# ---------------------------------------------------------------------------
# Slice 10 — integration tests (real HTTP; skipped unless -m integration)
# ---------------------------------------------------------------------------

def _first_installed_ollama_model() -> str:
    """Return the name of any installed Ollama model, or skip if none/unreachable.

    Avoids hard-coding a tag (e.g. bare ``gemma4``) that may not match what's
    actually pulled on the box (e.g. ``gemma4:e4b``) — a bare, uninstalled tag
    makes Ollama 404 on ``/api/generate``.
    """
    import httpx
    try:
        tags = httpx.get("http://localhost:11434/api/tags", timeout=2.0).json()
        models = [m["name"] for m in (tags.get("models") or [])]
    except Exception:
        pytest.skip("Ollama not reachable")
    if not models:
        pytest.skip("No Ollama models installed")
    return models[0]


@pytest.mark.integration
async def test_ollama_backend_real_call():
    """OllamaBackend hits live Ollama at localhost:11434."""
    from cerebral.llm.router import OllamaBackend
    backend = OllamaBackend(url="http://localhost:11434", model=_first_installed_ollama_model())
    result = await backend.complete("Reply with only the word PONG.", "chat")
    assert isinstance(result, str)
    assert len(result.strip()) > 0


@pytest.mark.integration
async def test_model_router_end_to_end_ollama():
    """ModelRouter with no injection calls real Ollama by default."""
    _first_installed_ollama_model()  # skip early if no local model is available
    router = ModelRouter()
    result = await router.complete("Reply with only the word PONG.", "chat")
    assert isinstance(result, str)
    assert len(result.strip()) > 0
    # The default picker selects the first installed ollama/* backend; don't
    # pin a specific tag (the box may have gemma4:e4b, qwen2.5:7b, etc.).
    assert router.active_model.startswith("ollama/")


@pytest.mark.integration
async def test_claw_backend_real_call():
    """ClawBackend hits live OpenClaw at localhost:3000 for Claude routing."""
    from cerebral.llm.router import ClawBackend
    backend = ClawBackend(url="http://localhost:3000")
    result = await backend.complete("Reply with only the word PONG.", "chat")
    assert isinstance(result, str)
    assert len(result.strip()) > 0


# ---------------------------------------------------------------------------
# Issue #271 — local Ollama timeout is generous + env-overridable
# ---------------------------------------------------------------------------

def test_ollama_timeout_defaults_to_180(monkeypatch):
    from cerebral.llm import router
    monkeypatch.delenv("OLLAMA_TIMEOUT_S", raising=False)
    assert router._ollama_timeout_s() == 180.0


def test_ollama_timeout_reads_env_override(monkeypatch):
    from cerebral.llm import router
    monkeypatch.setenv("OLLAMA_TIMEOUT_S", "300")
    assert router._ollama_timeout_s() == 300.0


def test_ollama_timeout_ignores_non_numeric(monkeypatch):
    from cerebral.llm import router
    monkeypatch.setenv("OLLAMA_TIMEOUT_S", "soon")
    assert router._ollama_timeout_s() == 180.0


def test_ollama_timeout_ignores_non_positive(monkeypatch):
    from cerebral.llm import router
    monkeypatch.setenv("OLLAMA_TIMEOUT_S", "0")
    assert router._ollama_timeout_s() == 180.0


async def test_ollama_complete_uses_configured_timeout(monkeypatch):
    """OllamaBackend.complete builds its client with the resolved timeout."""
    from cerebral.llm.router import OllamaBackend
    import httpx

    monkeypatch.setenv("OLLAMA_TIMEOUT_S", "240")
    captured = {}

    real_init = httpx.AsyncClient.__init__

    def spy_init(self, *args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return real_init(self, *args, **kwargs)

    async def fake_post(self, url, json=None):
        return httpx.Response(200, json={"response": "ok"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    backend = OllamaBackend(model="qwen2.5:7b")
    result = await backend.complete("hi", "chat")

    assert result == "ok"
    assert captured["timeout"] == 240.0
