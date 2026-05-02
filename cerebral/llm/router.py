"""
Model router — Issue #6.

Routes LLM completions to the active backend (Ollama/Gemma 4 by default, or
Claude via OpenClaw). Backends are injected, making the router fully testable
without live services.

Public interface:
  router = ModelRouter()                  # uses real HTTP backends
  await router.complete(prompt)           # → str
  router.switch_model("claude/haiku")     # takes effect immediately
  router.active_model                     # → current model id
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
        default_model: str = DEFAULT_MODEL,
    ):
        if backends is None:
            backends = _real_backends()
        if default_model not in backends:
            raise ValueError(f"default model '{default_model}' not in backends: {list(backends)}")
        self._backends = backends
        self._active_model = default_model

    @property
    def active_model(self) -> str:
        return self._active_model

    def switch_model(self, model_id: str) -> None:
        if model_id not in self._backends:
            raise ValueError(f"unknown model '{model_id}'; known: {list(self._backends)}")
        self._active_model = model_id
        logger.info("[router] active model → %s", model_id)

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        backend = self._backends[self._active_model]
        try:
            response = await backend.complete(prompt, task_type)
        except (OSError, ConnectionError) as exc:
            raise ModelUnavailableError(
                f"model '{self._active_model}' unavailable: {exc}"
            ) from exc
        logger.info("[router] %s handled request", self._active_model)
        return response


# ---------------------------------------------------------------------------
# Real HTTP backends (used when no injection is supplied)
# ---------------------------------------------------------------------------

def _real_backends() -> dict[str, Backend]:
    return {
        DEFAULT_MODEL: OllamaBackend(),
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
