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
        self._local_only = False

    @property
    def active_model(self) -> str:
        return self._active_model

    @property
    def local_only(self) -> bool:
        return self._local_only

    @property
    def last_model(self) -> str | None:
        return self._last_model

    @property
    def active_is_cloud(self) -> bool:
        return bool(self._models.get(self._active_model, {}).get("is_cloud", False))

    def list_models(self) -> list[dict]:
        # Local-only hides cloud entries entirely, so every model-picker surface
        # (Settings switch-list, per-task cards, tray submenu) shows local only.
        return [
            {
                "id": mid,
                "label": info.get("label", mid),
                "is_cloud": bool(info.get("is_cloud", False)),
                "is_active": mid == self._active_model,
                "is_last": mid == self._last_model,
                "is_custom": mid.startswith("custom/"),
            }
            for mid, info in self._models.items()
            if not (self._local_only and info.get("is_cloud"))
        ]

    def add_backend(self, model_id: str, backend: Backend, label: str, is_cloud: bool) -> None:
        """Register a user-added remote backend (custom/<slug>).

        Same registry the discovered/cloud backends live in, so it flows
        through list_models / switch_model / complete for free. Does not
        change the active model."""
        self._backends[model_id] = backend
        self._models[model_id] = {"label": label, "is_cloud": bool(is_cloud)}
        logger.info("[router] custom backend added → %s (cloud=%s)", model_id, is_cloud)

    def remove_backend(self, model_id: str) -> None:
        """Unregister a backend. If it was active or task-pinned, fall back to
        a local model (or the first remaining backend) — never silently to cloud
        beyond what was already active."""
        self._backends.pop(model_id, None)
        self._models.pop(model_id, None)
        for task_type, mid in list(self._task_models.items()):
            if mid == model_id:
                del self._task_models[task_type]
        if self._active_model == model_id:
            self._active_model = self._first_local() or next(iter(self._backends), model_id)
            logger.info("[router] active model fell back to %s after remove", self._active_model)

    def switch_model(self, model_id: str) -> None:
        if model_id not in self._backends:
            raise ValueError(f"unknown model '{model_id}'; known: {list(self._backends)}")
        if self._local_only and self._models.get(model_id, {}).get("is_cloud"):
            raise ValueError(f"cloud model '{model_id}' refused: local-only mode is on")
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
        if self._local_only and self._models.get(model_id, {}).get("is_cloud"):
            raise ValueError(f"cloud model '{model_id}' refused: local-only mode is on")
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
                if self._local_only and self._models.get(mid, {}).get("is_cloud"):
                    continue  # local-only: never seed a cloud quality model
                self.set_task_model(QUALITY_TASK, mid)
                return mid
        return None

    def _first_local(self) -> str | None:
        for mid in self._backends:
            if mid.startswith("ollama/"):
                return mid
        return None

    def set_local_only(self, enabled: bool) -> None:
        """Cloud kill-switch (privacy). When on, cloud backends are hidden from
        list_models(), refused by switch_model/set_task_model, and the active +
        per-task models are moved to local so nothing can route to Claude.
        """
        self._local_only = bool(enabled)
        if not self._local_only:
            return
        if self.active_is_cloud:
            local = self._first_local()
            if local:
                self._active_model = local
                logger.info("[router] local-only: active model moved to %s", local)
            # ponytail: no local installed -> active stays cloud (degenerate,
            # nothing can route). The Ollama-offline banner already flags this;
            # add a real fallback model only if that combo actually bites someone.
        for task_type, mid in list(self._task_models.items()):
            if self._models.get(mid, {}).get("is_cloud"):
                del self._task_models[task_type]
                logger.info("[router] local-only: cleared cloud mapping for task '%s'", task_type)

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


# Custom (user-added remote) model kinds → backend class + is_cloud. A remote
# Ollama is still "local-ish" inference the user runs, so local-only keeps it
# (is_cloud=False); OpenAI-compatible + Anthropic route off-box → is_cloud=True,
# hidden under the local-only kill-switch.
CUSTOM_KINDS = ("ollama", "openai", "anthropic")


def build_custom_backend(
    kind: str, url: str, model: str, api_key: str | None = None
) -> tuple[Backend, bool]:
    """Build a (backend, is_cloud) pair for a user-added remote model."""
    if kind == "ollama":
        return OllamaBackend(url=url, model=model), False
    if kind == "openai":
        return ClawBackend(url=url, model=model, api_key=api_key), True
    if kind == "anthropic":
        return AnthropicBackend(model=model, api_key=api_key), True
    raise ValueError(f"unknown custom model kind {kind!r}; known: {CUSTOM_KINDS}")


def _real_backends() -> dict[str, Backend]:
    """Build backends from whatever Ollama has installed + the fixed cloud entries.

    Ollama-offline path: returns just the cloud entries so cloud chat still works.
    Cloud entries use the Anthropic API directly when ANTHROPIC_API_KEY is set;
    otherwise they keep the legacy ClawBackend (OpenClaw) wiring.
    """
    backends: dict[str, Backend] = {}
    for name in OllamaBackend.list_installed_models():
        backends[f"ollama/{name}"] = OllamaBackend(model=name)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    for cid, info in CLOUD_MODELS.items():
        if api_key:
            backends[cid] = AnthropicBackend(model=info["claw_model"], api_key=api_key)
        else:
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
            # Ollama's default num_ctx (4096) silently truncates the tool
            # schemas + prompt. 8192 fits a 30-tool shortlist plus attachment
            # text, and its KV cache still fits alongside an 8B Q4 model on
            # an 8GB GPU (32k would not).
            "options": {"num_ctx": 8192},
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


def _normalize_openai_base(url: str) -> str:
    """Strip a trailing '/v1' and slashes so f'{url}/v1/chat/completions' works
    whether the user pastes the bare host or the full '.../v1' endpoint."""
    trimmed = (url or "").rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[:-3].rstrip("/")
    return trimmed


class ClawBackend:
    """Routes cloud LLM calls through OpenClaw's inference layer.

    Also handles user-added OpenAI-compatible servers (issue #523): pass an
    ``api_key`` and it sends ``Authorization: Bearer <key>``; a trailing
    ``/v1`` in the URL is stripped once so pasting the natural ``.../v1``
    endpoint works as well as the bare host.
    """

    def __init__(
        self,
        url: str = "http://localhost:3000",
        model: str = "claude-haiku-4-5-20251001",
        api_key: str | None = None,
    ):
        self.url = _normalize_openai_base(url)
        self.model = model
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import httpx
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise ConnectionError(
                    f"HTTP {exc.response.status_code} from {exc.request.url}"
                ) from exc
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
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise ConnectionError(
                    f"HTTP {exc.response.status_code} from {exc.request.url}"
                ) from exc

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


class AnthropicBackend:
    """Calls the Anthropic API directly via the official SDK.

    Used for the CLOUD_MODELS entries when ANTHROPIC_API_KEY is set —
    OpenClaw 2026.5.28 exposes no HTTP inference endpoint, so ClawBackend's
    /v1/chat/completions path never worked live (issue #378). Tool schemas
    from ``tools_for_llm`` are already the Anthropic tool-use format, so
    they pass through untranslated.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        api_key: str | None = None,
        client=None,  # injectable AsyncAnthropic for tests
    ):
        self.model = model
        self._api_key = api_key
        self._client = client

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import anthropic

        try:
            resp = await self._get_client().messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            raise ConnectionError(str(exc)) from exc
        return "".join(b.text for b in resp.content if b.type == "text")

    async def complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> ToolCall | str:
        """Native Anthropic tool use; ToolCall on tool_use, str otherwise."""
        import anthropic

        try:
            resp = await self._get_client().messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
            )
        except anthropic.APIError as exc:
            raise ConnectionError(str(exc)) from exc
        for block in resp.content:
            if block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                return ToolCall(name=block.name, args=args)
        return "".join(b.text for b in resp.content if b.type == "text")
