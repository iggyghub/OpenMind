"""
Model router — Issue #6 + Issue #29 + Issue #37.

Routes LLM completions to the active backend (whichever Ollama model is
currently installed, or Claude via OpenClaw). Backends are injected, making
the router fully testable without live services.

Public interface:
  router = ModelRouter()                                # uses real HTTP backends
  await router.complete(prompt, task_type="chat")       # → str
  router.switch_model("claude/haiku")                   # active model for any task
  router.set_task_model("extraction", "ollama/...")     # per-task override
  router.get_task_model("extraction")                   # → resolved model id
  router.list_models()                                  # → [{id,label,is_cloud,...}]
  router.active_model                                   # → current model id
  router.last_model                                     # → id of last to handle a request
  router.active_is_cloud                                # → True if active is a cloud model
  router.refresh_local_backends(tags_fetch_fn=None)     # re-query Ollama, update picker
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Local inference on a CPU-bound box can take 80s+ for a real (large) prompt,
# so the default Ollama timeout is generous and env-overridable. Cloud
# (ClawBackend) stays at 60s because cloud inference is fast. See issue #271.
_DEFAULT_OLLAMA_TIMEOUT_S = 180.0


def _ollama_timeout_s() -> float:
    """Local-inference HTTP timeout in seconds (override via OLLAMA_TIMEOUT_S)."""
    raw = os.environ.get("OLLAMA_TIMEOUT_S")
    if raw is None:
        return _DEFAULT_OLLAMA_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[router] OLLAMA_TIMEOUT_S=%r is not a number; using default %ss",
            raw, _DEFAULT_OLLAMA_TIMEOUT_S,
        )
        return _DEFAULT_OLLAMA_TIMEOUT_S
    if value <= 0:
        logger.warning(
            "[router] OLLAMA_TIMEOUT_S=%r must be > 0; using default %ss",
            raw, _DEFAULT_OLLAMA_TIMEOUT_S,
        )
        return _DEFAULT_OLLAMA_TIMEOUT_S
    return value


class ModelUnavailableError(Exception):
    """Raised when the active backend cannot be reached. Never silently falls back."""


@dataclass
class ToolCall:
    """A tool invocation returned by the LLM planner (Issue #274)."""
    name: str
    args: dict = field(default_factory=dict)


@runtime_checkable
class Backend(Protocol):
    async def complete(self, prompt: str, task_type: str) -> str: ...
    async def complete_with_tools(self, prompt: str, tools: list[dict]) -> ToolCall | str: ...


# Preferred models for the "quality" task type (issue #349), best first.
# Local-first: qwen3:8b gives better args than qwen2.5:7b (A/B 2026-07-03)
# and still fits the 8GB card; cloud Sonnet is the fallback when it isn't
# pulled. Correctness-critical code paths (jobs field-mapping, fit-scoring,
# dossier extraction) tag task_type="quality" to land here.
QUALITY_TASK = "quality"
QUALITY_PREFERRED = ("ollama/qwen3:8b", "claude/sonnet")

# Cloud entries are constants; local entries are discovered at runtime.
CLOUD_MODELS = {
    "claude/haiku":  {"label": "Claude Haiku 4.5",  "is_cloud": True,
                      "claw_model": "claude-haiku-4-5-20251001"},
    "claude/sonnet": {"label": "Claude Sonnet 4.6", "is_cloud": True,
                      "claw_model": "claude-sonnet-4-6"},
}


class ModelRouter:
    def __init__(
        self,
        backends: dict[str, Backend] | None = None,
        models: dict[str, dict] | None = None,
        default_model: str | None = None,
    ):
        if backends is None:
            backends = _real_backends()
            if models is None:
                models = _real_models(backends)
        if not backends:
            raise ValueError("no model backends available")
        if default_model is None:
            default_model = _pick_default(backends)
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

    def seed_quality_default(self) -> str | None:
        """Seed the default "quality" mapping (issue #349): first installed
        model from QUALITY_PREFERRED wins. Returns the chosen id, or None
        when none is installed — "quality" then resolves to the active model.
        """
        for mid in QUALITY_PREFERRED:
            if mid in self._backends:
                self.set_task_model(QUALITY_TASK, mid)
                return mid
        return None

    def refresh_local_backends(
        self,
        tags_fetch_fn: Callable[[str], dict] | None = None,
        url: str = "http://localhost:11434",
    ) -> list[str]:
        """Re-query Ollama and update local (`ollama/*`) backends in place.

        Cloud backends are preserved. If the active model was uninstalled,
        falls back to another available backend. Returns the list of
        currently-installed `ollama/*` model ids.
        """
        for mid in list(self._backends):
            if mid.startswith("ollama/"):
                del self._backends[mid]
                self._models.pop(mid, None)

        new_ids: list[str] = []
        for name in OllamaBackend.list_installed_models(url=url, tags_fetch_fn=tags_fetch_fn):
            mid = f"ollama/{name}"
            self._backends[mid] = OllamaBackend(url=url, model=name)
            self._models[mid] = {"label": name, "is_cloud": False}
            new_ids.append(mid)

        if self._active_model not in self._backends:
            # Prefer a freshly-discovered local model; otherwise first remaining backend.
            self._active_model = new_ids[0] if new_ids else next(iter(self._backends), self._active_model)
            logger.info("[router] active model fell back to %s after refresh", self._active_model)

        # Drop any per-task mappings that point to uninstalled models.
        for task_type, mid in list(self._task_models.items()):
            if mid not in self._backends:
                del self._task_models[task_type]

        return new_ids

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        model_id = self._task_models.get(task_type, self._active_model)
        # Graceful fallback (issue #349): a per-task model that is missing or
        # unreachable falls back to the active model with a log. The active
        # model itself failing still raises — never a silent cloud fallback.
        if model_id not in self._backends:
            logger.warning(
                "[router] task '%s' model '%s' not available — using active %s",
                task_type, model_id, self._active_model,
            )
            model_id = self._active_model
        try:
            response = await self._backends[model_id].complete(prompt, task_type)
        except (OSError, ConnectionError) as exc:
            if model_id == self._active_model:
                raise ModelUnavailableError(
                    f"model '{model_id}' unavailable: {exc}"
                ) from exc
            logger.warning(
                "[router] task '%s' model '%s' unavailable (%s) — falling back to active %s",
                task_type, model_id, exc, self._active_model,
            )
            model_id = self._active_model
            try:
                response = await self._backends[model_id].complete(prompt, task_type)
            except (OSError, ConnectionError) as exc2:
                raise ModelUnavailableError(
                    f"model '{model_id}' unavailable: {exc2}"
                ) from exc2
        self._last_model = model_id
        logger.info("[router] %s handled request", model_id)
        return response

    async def complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> ToolCall | str:
        """Route a tool-selection request to the active backend (Issue #274)."""
        model_id = self._task_models.get("tool", self._active_model)
        backend = self._backends[model_id]
        try:
            result = await backend.complete_with_tools(prompt, tools)
        except (OSError, ConnectionError) as exc:
            raise ModelUnavailableError(
                f"model '{model_id}' unavailable: {exc}"
            ) from exc
        self._last_model = model_id
        logger.info("[router] %s handled tool-selection request", model_id)
        return result


# ---------------------------------------------------------------------------
# Default-picker + real-backend factories
# ---------------------------------------------------------------------------

def _pick_default(backends: dict[str, Backend]) -> str:
    """Pick the first ollama/* backend if any, otherwise the first backend."""
    for mid in backends:
        if mid.startswith("ollama/"):
            return mid
    return next(iter(backends))


def _real_backends() -> dict[str, Backend]:
    """Build backends from whatever Ollama has installed + the fixed cloud entries.

    Ollama-offline path: returns just the cloud entries so cloud chat still works.
    """
    backends: dict[str, Backend] = {}
    for name in OllamaBackend.list_installed_models():
        backends[f"ollama/{name}"] = OllamaBackend(model=name)
    for cid, info in CLOUD_MODELS.items():
        backends[cid] = ClawBackend(model=info["claw_model"])
    return backends


def _real_models(backends: dict[str, Backend]) -> dict[str, dict]:
    """Metadata for both discovered Ollama models and the fixed cloud entries."""
    models: dict[str, dict] = {}
    for mid in backends:
        if mid.startswith("ollama/"):
            models[mid] = {"label": mid.split("/", 1)[1], "is_cloud": False}
    for cid, info in CLOUD_MODELS.items():
        if cid in backends:
            models[cid] = {"label": info["label"], "is_cloud": True}
    return models


class OllamaBackend:
    """Calls Ollama's generate endpoint directly (local, no cloud dependency)."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "qwen2.5:7b"):
        self.url = url
        self.model = model

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import httpx
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        async with httpx.AsyncClient(timeout=_ollama_timeout_s()) as client:
            try:
                resp = await client.post(f"{self.url}/api/generate", json=payload)
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc
        return resp.json()["response"]

    async def complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> ToolCall | str:
        """Tool-selection via Ollama /api/chat with native tool-calling (Issue #274)."""
        import httpx
        import json as _json

        ollama_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "tools": ollama_tools,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=_ollama_timeout_s()) as client:
            try:
                resp = await client.post(f"{self.url}/api/chat", json=payload)
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc

        message = resp.json()["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            fn = tool_calls[0]["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    args = {}
            return ToolCall(name=fn["name"], args=args)
        return message.get("content") or ""

    @staticmethod
    def list_installed_models(
        url: str = "http://localhost:11434",
        tags_fetch_fn: Callable[[str], dict] | None = None,
    ) -> list[str]:
        """Return model names from Ollama's `/api/tags`. Empty list if unreachable."""
        if tags_fetch_fn is None:
            tags_fetch_fn = _http_tags_fetch
        try:
            payload = tags_fetch_fn(url)
        except (OSError, ConnectionError) as exc:
            logger.warning("[router] Ollama unreachable — no local models available (%s)", exc)
            return []
        return [m["name"] for m in (payload.get("models") or [])]


def _http_tags_fetch(url: str) -> dict:
    """Default Ollama tags fetcher — synchronous httpx call."""
    import httpx
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=2.0)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        raise ConnectionError(str(exc)) from exc
    return resp.json()


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

    async def complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> ToolCall | str:
        """Tool-selection via OpenClaw /v1/chat/completions (Issue #274).

        Fail-soft: if the response has no tool_calls (some OpenClaw builds drop
        them), return the plain text content rather than raising.
        """
        import httpx
        import json as _json

        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "tools": oai_tools,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{self.url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc

        message = resp.json()["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            fn = tool_calls[0]["function"]
            args = fn.get("arguments") or "{}"
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    args = {}
            return ToolCall(name=fn["name"], args=args)
        # Fail-soft: no tool_calls → return text content
        return message.get("content") or ""
