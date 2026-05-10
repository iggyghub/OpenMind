"""
Model router — Issue #6 + Issue #29.

Routes LLM completions to the active backend (Ollama/Gemma 4 by default, or
Claude via OpenClaw). Backends are injected, making the router fully testable
without live services.

Public interface:
  router = ModelRouter()                                # uses real HTTP backends
  await router.complete(prompt, task_type="chat")       # → str
  router.switch_model("claude/haiku")                   # active model for any task
  router.set_task_model("extraction", "ollama/gemma4")  # per-task override
  router.get_task_model("extraction")                   # → resolved model id
  router.list_models()                                  # → [{id,label,is_cloud,...}]
  router.active_model                                   # → current model id
  router.last_model                                     # → id of last to handle a request
  router.active_is_cloud                                # → True if active is a cloud model
"""

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class ModelUnavailableError(Exception):
    """Raised when the active backend cannot be reached. Never silently falls back."""


@runtime_checkable
class Backend(Protocol):
    async def complete(self, prompt: str, task_type: str) -> str: ...


DEFAULT_MODEL = "ollama/gemma4"


class ModelRouter:
    def __init__(
        self,
        backends: dict[str, Backend] | None = None,
        models: dict[str, dict] | None = None,
        default_model: str = DEFAULT_MODEL,
    ):
        if backends is None:
            backends = _real_backends()
            if models is None:
                models = _real_models()
        if default_model not in backends:
            raise ValueError(f"default model '{default_model}' not in backends: {list(backends)}")
        if models is None:
            models = {mid: {"label": mid, "is_cloud": False} for mid in backends}
        # Ensure every backend has a metadata entry
        for mid in backends:
            models.setdefault(mid, {"label": mid, "is_cloud": False})
        self._backends = backends
        self._models = models
        self._active_model = default_model
        self._last_model: str | None = None
        self._task_models: dict[str, str] = {}

    @property
    def active_model(self) -> str:
        return self._active_model

    @property
    def last_model(self) -> str | None:
        return self._last_model

    @property
    def active_is_cloud(self) -> bool:
        return bool(self._models.get(self._active_model, {}).get("is_cloud", False))

    def list_models(self) -> list[dict]:
        return [
            {
                "id": mid,
                "label": info.get("label", mid),
                "is_cloud": bool(info.get("is_cloud", False)),
                "is_active": mid == self._active_model,
                "is_last": mid == self._last_model,
            }
            for mid, info in self._models.items()
        ]

    def switch_model(self, model_id: str) -> None:
        if model_id not in self._backends:
            raise ValueError(f"unknown model '{model_id}'; known: {list(self._backends)}")
        self._active_model = model_id
        logger.info("[router] active model → %s", model_id)

    def set_task_model(self, task_type: str, model_id: str | None) -> None:
        """Pin a model for a specific task type. Pass model_id=None to clear."""
        if model_id is None:
            self._task_models.pop(task_type, None)
            logger.info("[router] task '%s' mapping cleared", task_type)
            return
        if model_id not in self._backends:
            raise ValueError(f"unknown model '{model_id}'; known: {list(self._backends)}")
        self._task_models[task_type] = model_id
        logger.info("[router] task '%s' → %s", task_type, model_id)

    def get_task_model(self, task_type: str) -> str:
        """Resolve which model id will handle a given task type."""
        return self._task_models.get(task_type, self._active_model)

    def task_models(self) -> dict[str, str]:
        return dict(self._task_models)

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        model_id = self._task_models.get(task_type, self._active_model)
        backend = self._backends[model_id]
        try:
            response = await backend.complete(prompt, task_type)
        except (OSError, ConnectionError) as exc:
            raise ModelUnavailableError(
                f"model '{model_id}' unavailable: {exc}"
            ) from exc
        self._last_model = model_id
        logger.info("[router] %s handled request", model_id)
        return response


# ---------------------------------------------------------------------------
# Real HTTP backends (used when no injection is supplied)
# ---------------------------------------------------------------------------

def _real_backends() -> dict[str, Backend]:
    return {
        DEFAULT_MODEL: OllamaBackend(),
        "claude/haiku": ClawBackend(model="claude-haiku-4-5-20251001"),
        "claude/sonnet": ClawBackend(model="claude-sonnet-4-6"),
    }


def _real_models() -> dict[str, dict]:
    return {
        DEFAULT_MODEL: {"label": "Gemma 4 (local)", "is_cloud": False},
        "claude/haiku": {"label": "Claude Haiku 4.5", "is_cloud": True},
        "claude/sonnet": {"label": "Claude Sonnet 4.6", "is_cloud": True},
    }


class OllamaBackend:
    """Calls Ollama's generate endpoint directly (local, no cloud dependency)."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "gemma4"):
        self.url = url
        self.model = model

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import httpx
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{self.url}/api/generate", json=payload)
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc
        return resp.json()["response"]


class ClawBackend:
    """Routes cloud LLM calls through OpenClaw's inference layer."""

    def __init__(
        self,
        url: str = "http://localhost:3000",
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.url = url
        self.model = model

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import httpx
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{self.url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc
        return resp.json()["choices"][0]["message"]["content"]
