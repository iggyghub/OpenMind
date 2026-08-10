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
# so the default Ollama timeout is generous and env-overridable. See issue #271.
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


# A custom OpenAI-compatible endpoint (ClawBackend, kind="openai") may serve a
# slow/thinking model that reasons for minutes on a large prompt -- self_dev
# edits timed out at the old hardcoded 60s (empty httpx.TimeoutException ->
# "model unavailable"). Env-overridable with a generous default. OpenClaw's own
# cloud path is fast, but a user's custom server (e.g. a Hermes/Qwen agent) is
# not guaranteed to be.
_DEFAULT_CLAW_TIMEOUT_S = 300.0


def _claw_timeout_s() -> float:
    """ClawBackend / custom-endpoint HTTP timeout in seconds (override via CLAW_TIMEOUT_S)."""
    raw = os.environ.get("CLAW_TIMEOUT_S")
    if raw is None:
        return _DEFAULT_CLAW_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[router] CLAW_TIMEOUT_S=%r is not a number; using default %ss",
            raw, _DEFAULT_CLAW_TIMEOUT_S,
        )
        return _DEFAULT_CLAW_TIMEOUT_S
    if value <= 0:
        logger.warning(
            "[router] CLAW_TIMEOUT_S=%r must be > 0; using default %ss",
            raw, _DEFAULT_CLAW_TIMEOUT_S,
        )
        return _DEFAULT_CLAW_TIMEOUT_S
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
    # supports_vision: True when the backend's configured model can ground on
    # image bytes. ModelRouter.complete_with_images walks the priority chain
    # and picks the first backend where this is True (ADR-0016 sec 5).
    supports_vision: bool

    async def complete(self, prompt: str, task_type: str) -> str: ...
    async def complete_with_tools(self, prompt: str, tools: list[dict]) -> ToolCall | str: ...
    async def complete_with_images(
        self, prompt: str, images: list[bytes], task_type: str
    ) -> str: ...


# ADR-0016 sec 5: pixel-vision grounding is routed on this task_type so the
# priority chain (local -> Budd -> cloud) picks the first VL-capable backend.
VISION_TASK = "computer_use_vision"


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
        self._last_model: str | None = None
        self._task_models: dict[str, str] = {}
        self._local_only = False
        # Priority list + enabled-set + master fallback toggle (P1 #531).
        # active_model is derived: first enabled + routable model in priority
        # order. switch_model moves an id to the top.
        self._priority: list[str] = (
            [default_model] + [mid for mid in backends if mid != default_model]
        )
        self._enabled: dict[str, bool] = {mid: True for mid in backends}
        self._fallback_enabled: bool = False

    @property
    def active_model(self) -> str:
        for mid in self._priority:
            if mid not in self._backends:
                continue
            if not self._enabled.get(mid, True):
                continue
            if self._local_only and self._models.get(mid, {}).get("is_cloud"):
                continue
            return mid
        # Degenerate: nothing routable. Fall back to the top of the priority
        # list (even if disabled/hidden) or the first backend id.
        for mid in self._priority:
            if mid in self._backends:
                return mid
        return next(iter(self._backends), "")

    @property
    def local_only(self) -> bool:
        return self._local_only

    @property
    def last_model(self) -> str | None:
        return self._last_model

    @property
    def active_is_cloud(self) -> bool:
        return bool(self._models.get(self.active_model, {}).get("is_cloud", False))

    @property
    def fallback_enabled(self) -> bool:
        return self._fallback_enabled

    def priority(self) -> list[str]:
        return list(self._priority)

    def enabled_map(self) -> dict[str, bool]:
        return {mid: bool(self._enabled.get(mid, True)) for mid in self._priority}

    def list_models(self) -> list[dict]:
        # Local-only hides cloud entries entirely, so every model-picker surface
        # (Settings switch-list, per-task cards, tray submenu) shows local only.
        top = self.active_model
        out: list[dict] = []
        position = 0
        for mid in self._priority:
            if mid not in self._backends:
                continue
            info = self._models.get(mid, {})
            if self._local_only and info.get("is_cloud"):
                continue
            out.append({
                "id": mid,
                "label": info.get("label", mid),
                "is_cloud": bool(info.get("is_cloud", False)),
                "is_active": mid == top,
                "is_last": mid == self._last_model,
                "is_custom": mid.startswith("custom/"),
                "enabled": bool(self._enabled.get(mid, True)),
                "position": position,
            })
            position += 1
        return out

    def set_priority(self, order: list[str]) -> None:
        """Replace the priority ordering. Unknown ids are dropped, known-but-missing
        backends are appended at the end so the router keeps a full row."""
        seen: set[str] = set()
        cleaned: list[str] = []
        for mid in order:
            if mid in self._backends and mid not in seen:
                cleaned.append(mid)
                seen.add(mid)
        for mid in self._backends:
            if mid not in seen:
                cleaned.append(mid)
        self._priority = cleaned
        logger.info("[router] priority set: %s", cleaned)

    def set_model_enabled(self, model_id: str, enabled: bool) -> None:
        if model_id not in self._backends:
            raise ValueError(
                f"unknown model '{model_id}'; known: {list(self._backends)}"
            )
        self._enabled[model_id] = bool(enabled)
        logger.info(
            "[router] model %s %s", model_id, "enabled" if enabled else "disabled"
        )

    def set_fallback(self, enabled: bool) -> None:
        self._fallback_enabled = bool(enabled)
        logger.info(
            "[router] master fallback %s", "enabled" if enabled else "disabled"
        )

    def add_backend(self, model_id: str, backend: Backend, label: str, is_cloud: bool) -> None:
        """Register a user-added remote backend (custom/<slug>).

        Same registry the discovered/cloud backends live in, so it flows
        through list_models / switch_model / complete for free. Appended at
        the bottom of the priority list, enabled by default."""
        self._backends[model_id] = backend
        self._models[model_id] = {"label": label, "is_cloud": bool(is_cloud)}
        if model_id not in self._priority:
            self._priority.append(model_id)
        self._enabled.setdefault(model_id, True)
        logger.info("[router] custom backend added → %s (cloud=%s)", model_id, is_cloud)

    def remove_backend(self, model_id: str) -> None:
        """Unregister a backend. Drops from priority + enabled + task pins.
        active_model derives from what's left."""
        self._backends.pop(model_id, None)
        self._models.pop(model_id, None)
        self._enabled.pop(model_id, None)
        self._priority = [m for m in self._priority if m != model_id]
        for task_type, mid in list(self._task_models.items()):
            if mid == model_id:
                del self._task_models[task_type]

    def switch_model(self, model_id: str) -> None:
        if model_id not in self._backends:
            raise ValueError(f"unknown model '{model_id}'; known: {list(self._backends)}")
        if self._local_only and self._models.get(model_id, {}).get("is_cloud"):
            raise ValueError(f"cloud model '{model_id}' refused: local-only mode is on")
        # switch_model moves the id to the top of priority and re-enables it,
        # so the derived active_model follows.
        self._priority = [model_id] + [m for m in self._priority if m != model_id]
        self._enabled[model_id] = True
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
        return self._task_models.get(task_type, self.active_model)

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

    def set_local_only(self, enabled: bool) -> None:
        """Cloud kill-switch (privacy). When on, cloud backends are hidden from
        list_models(), refused by switch_model/set_task_model, and excluded
        from routing so nothing can route to Claude. active_model is derived
        and skips cloud automatically."""
        self._local_only = bool(enabled)
        if not self._local_only:
            return
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

        Cloud + custom backends are preserved. Newly-discovered locals are
        appended at the end of priority (enabled by default). active_model
        derives from what remains. Returns the list of currently-installed
        `ollama/*` model ids.
        """
        for mid in list(self._backends):
            if mid.startswith("ollama/"):
                del self._backends[mid]
                self._models.pop(mid, None)
                # Keep _enabled: a model the user disabled must stay disabled
                # across a refresh. setdefault below only fills NEW ids, so a
                # surviving/reinstalled model keeps its prior toggle instead of
                # snapping back to enabled (which _persist_priority would then
                # write to the DB, un-disabling it permanently).
        self._priority = [m for m in self._priority if not m.startswith("ollama/")]

        new_ids: list[str] = []
        for name in OllamaBackend.list_installed_models(url=url, tags_fetch_fn=tags_fetch_fn):
            mid = f"ollama/{name}"
            self._backends[mid] = OllamaBackend(url=url, model=name)
            self._models[mid] = {"label": name, "is_cloud": False}
            self._enabled.setdefault(mid, True)
            new_ids.append(mid)
        # Newly-discovered locals go to the end of priority so a user's
        # existing ordering (from set_priority / switch_model) survives.
        self._priority = self._priority + new_ids

        # Drop any per-task mappings that point to uninstalled models.
        for task_type, mid in list(self._task_models.items()):
            if mid not in self._backends:
                del self._task_models[task_type]

        return new_ids

    def _routable_chain(self) -> list[str]:
        """Enabled + non-hidden + present-in-backends models, in priority order."""
        out: list[str] = []
        for mid in self._priority:
            if mid not in self._backends:
                continue
            if not self._enabled.get(mid, True):
                continue
            if self._local_only and self._models.get(mid, {}).get("is_cloud"):
                continue
            out.append(mid)
        return out

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        top = self.active_model
        model_id = self._task_models.get(task_type, top)
        # Graceful fallback (issue #349): a per-task model that is missing or
        # unreachable falls back to the active model with a log. The active
        # model itself failing still raises — never a silent cloud fallback
        # unless the master fallback toggle is on.
        if model_id not in self._backends:
            logger.warning(
                "[router] task '%s' model '%s' not available — using active %s",
                task_type, model_id, top,
            )
            model_id = top

        if self._fallback_enabled:
            chain = self._routable_chain()
            attempts = [model_id] + [m for m in chain if m != model_id]
            attempts = [m for m in attempts if m in self._backends]
            last_exc: Exception | None = None
            for mid in attempts:
                try:
                    response = await self._backends[mid].complete(prompt, task_type)
                except (OSError, ConnectionError) as exc:
                    last_exc = exc
                    logger.warning(
                        "[router] fallback: '%s' unavailable (%s) — trying next", mid, exc,
                    )
                    continue
                self._last_model = mid
                logger.info("[router] %s handled request", mid)
                return response
            raise ModelUnavailableError(
                f"all enabled models unavailable: {last_exc}"
            ) from last_exc

        try:
            response = await self._backends[model_id].complete(prompt, task_type)
        except (OSError, ConnectionError) as exc:
            if model_id == top:
                raise ModelUnavailableError(
                    f"model '{model_id}' unavailable: {exc}"
                ) from exc
            logger.warning(
                "[router] task '%s' model '%s' unavailable (%s) — falling back to active %s",
                task_type, model_id, exc, top,
            )
            model_id = top
            try:
                response = await self._backends[model_id].complete(prompt, task_type)
            except (OSError, ConnectionError) as exc2:
                raise ModelUnavailableError(
                    f"model '{model_id}' unavailable: {exc2}"
                ) from exc2
        self._last_model = model_id
        logger.info("[router] %s handled request", model_id)
        return response

    def _vision_chain(self) -> list[str]:
        """Priority-ordered enabled + routable backends that self-declare vision.
        Honors local_only via _routable_chain (cloud tiers excluded)."""
        return [
            mid for mid in self._routable_chain()
            if getattr(self._backends[mid], "supports_vision", False)
        ]

    async def complete_with_images(
        self, prompt: str, images: list[bytes], task_type: str = VISION_TASK
    ) -> str:
        """Route a grounding request through the priority chain (ADR-0016 sec 5).

        First VL-capable backend in priority order wins; a tier without a
        vision-capable model is skipped. ConnectionError on a picked tier
        falls through to the next VL tier. Raises ModelUnavailableError if
        no VL tier is reachable (local-only + no local VL model -> raise;
        caller escalates to attended-handoff per ADR-0016 sec 5)."""
        chain = self._vision_chain()
        if not chain:
            raise ModelUnavailableError(
                "no vision-capable model available "
                f"(task_type={task_type!r}, local_only={self._local_only})"
            )
        last_exc: Exception | None = None
        for mid in chain:
            try:
                response = await self._backends[mid].complete_with_images(
                    prompt, images, task_type
                )
            except (OSError, ConnectionError) as exc:
                last_exc = exc
                logger.warning(
                    "[router] vision: '%s' unavailable (%s) -- trying next", mid, exc,
                )
                continue
            self._last_model = mid
            logger.info("[router] %s handled vision request", mid)
            return response
        raise ModelUnavailableError(
            f"all vision-capable models unavailable: {last_exc}"
        ) from last_exc

    async def complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> ToolCall | str:
        """Route a tool-selection request to the active backend (Issue #274)."""
        model_id = self._task_models.get("tool", self.active_model)
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
    kind: str, url: str, model: str, api_key: str | None = None,
    supports_vision: bool = False,
) -> tuple[Backend, bool]:
    """Build a (backend, is_cloud) pair for a user-added remote model."""
    if kind == "ollama":
        return OllamaBackend(url=url, model=model), False
    if kind == "openai":
        return ClawBackend(url=url, model=model, api_key=api_key,
                           supports_vision=supports_vision), True
    if kind == "anthropic":
        return AnthropicBackend(model=model, api_key=api_key), True
    raise ValueError(f"unknown custom model kind {kind!r}; known: {CUSTOM_KINDS}")


# Dynamic (server-first) custom kinds: kinds whose model can be auto-resolved
# from the server. Anthropic has no model-listing endpoint we can rely on, so
# it's pinned-only.
DYNAMIC_CUSTOM_KINDS = ("ollama", "openai")


def dynamic_is_cloud(kind: str) -> bool:
    """is_cloud for a dynamic custom kind — mirrors build_custom_backend."""
    return kind == "openai"


class DynamicModelBackend:
    """Server-first custom backend: model auto-resolved from the server (S3 #525).

    Lazy: no network at construction, so `_restore_custom_models` stays
    offline-safe. On complete/complete_with_tools, if no model is cached,
    resolves from `/v1/models` (openai) or `/api/tags` (ollama), picks the
    first entry, caches it, then delegates. On a not-found (HTTP 404) from
    the delegate, re-resolves once and retries -- catches "server swapped
    its underlying model" without user action. Any further failure raises
    ConnectionError so the router surfaces ModelUnavailableError.
    """

    def __init__(
        self,
        kind: str,
        url: str,
        cached_model: str = "",
        api_key: str | None = None,
        on_resolved: Callable[[str], None] | None = None,
        supports_vision: bool = False,
        # Test seams -- inject to avoid live HTTP.
        openai_list_fn: Callable[[str, str | None], list[str]] | None = None,
        ollama_list_fn: Callable[[str], list[str]] | None = None,
    ):
        if kind not in DYNAMIC_CUSTOM_KINDS:
            raise ValueError(
                f"dynamic model requires kind in {DYNAMIC_CUSTOM_KINDS}; got {kind!r}"
            )
        self.kind = kind
        self.url = url
        self.api_key = api_key
        self._model = cached_model or ""
        self.on_resolved = on_resolved
        # Dynamic backends can't auto-detect VL, so they default off; the user
        # flips this on (supports_vision) when the endpoint serves a VL model.
        self.supports_vision = supports_vision
        self._openai_list = openai_list_fn or (
            lambda u, k: list_openai_models(u, api_key=k)
        )
        self._ollama_list = ollama_list_fn or (
            lambda u: OllamaBackend.list_installed_models(url=u)
        )

    @property
    def model(self) -> str:
        return self._model

    def _resolve(self) -> str:
        names = (
            self._openai_list(self.url, self.api_key)
            if self.kind == "openai"
            else self._ollama_list(self.url)
        )
        if not names:
            raise ConnectionError(
                f"no models available at {self.url} (dynamic {self.kind})"
            )
        self._model = names[0]
        if self.on_resolved:
            self.on_resolved(self._model)
        return self._model

    def _inner(self) -> Backend:
        return build_custom_backend(self.kind, self.url, self._model, self.api_key)[0]

    async def _call(self, method: str, *args):
        if not self._model:
            self._resolve()
        try:
            return await getattr(self._inner(), method)(*args)
        except ConnectionError as exc:
            if "HTTP 404" not in str(exc):
                raise
            # Server swapped the model out from under us — re-resolve once, retry.
            self._model = ""
            self._resolve()
            return await getattr(self._inner(), method)(*args)

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        return await self._call("complete", prompt, task_type)

    async def complete_with_tools(
        self, prompt: str, tools: list[dict]
    ) -> ToolCall | str:
        return await self._call("complete_with_tools", prompt, tools)

    async def complete_with_images(
        self, prompt: str, images: list[bytes], task_type: str = VISION_TASK
    ) -> str:
        return await self._call("complete_with_images", prompt, images, task_type)


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

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        supports_vision: bool = False,
    ):
        self.url = url
        self.model = model
        # ADR-0016 sec 5: opt-in per instance -- Ollama VL models (llava,
        # qwen2.5vl, ...) advertise this so the router's vision chain picks
        # them; text-only Ollama tiers stay skipped.
        self.supports_vision = supports_vision

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import httpx
        # num_ctx: Ollama's default (4096) silently truncates long prompts --
        # a self_dev edit ships whole source files and needs the headroom
        # (mirrors complete_with_tools, Issue #274).
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": {"num_ctx": 8192}}
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

    async def complete_with_images(
        self, prompt: str, images: list[bytes], task_type: str = VISION_TASK
    ) -> str:
        """Multimodal generate via Ollama's /api/generate `images` field.

        Images are base64-encoded strings alongside the prompt. Requires a
        VL model (llava, qwen2.5vl, ...); a text-only model will 400 or
        return garbage -- gate via supports_vision so the router skips it."""
        import base64
        import httpx
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [base64.b64encode(img).decode("ascii") for img in images],
            "stream": False,
            "options": {"num_ctx": 8192},
        }
        async with httpx.AsyncClient(timeout=_ollama_timeout_s()) as client:
            try:
                resp = await client.post(f"{self.url}/api/generate", json=payload)
                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise ConnectionError(str(exc)) from exc
        return resp.json()["response"]

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


def _http_models_fetch(url: str, headers: dict) -> dict:
    """Default OpenAI /v1/models fetcher — synchronous httpx GET."""
    import httpx
    try:
        resp = httpx.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        raise ConnectionError(str(exc)) from exc
    return resp.json()


def list_openai_models(
    url: str,
    api_key: str | None = None,
    fetch_fn: Callable[[str, dict], dict] | None = None,
) -> list[str]:
    """GET {normalized_base}/v1/models; return model ids. [] when unreachable.

    fetch_fn(url, headers) -> dict for tests; defaults to a blocking httpx GET.
    Reuses _normalize_openai_base so a trailing /v1 in the URL is handled once.
    """
    if fetch_fn is None:
        fetch_fn = _http_models_fetch
    base = _normalize_openai_base(url)
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        payload = fetch_fn(f"{base}/v1/models", headers)
    except (OSError, ConnectionError) as exc:
        logger.warning("[router] model list unreachable (%s)", exc)
        return []
    return [m["id"] for m in (payload.get("data") or [])]


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
        supports_vision: bool = False,
    ):
        self.url = _normalize_openai_base(url)
        self.model = model
        self.api_key = api_key
        # ADR-0016 sec 5: Budd is the expected VL tier in practice; set True
        # when the configured model at this endpoint is multimodal.
        self.supports_vision = supports_vision

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        import httpx
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=_claw_timeout_s()) as client:
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
        async with httpx.AsyncClient(timeout=_claw_timeout_s()) as client:
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


    async def complete_with_images(
        self, prompt: str, images: list[bytes], task_type: str = VISION_TASK
    ) -> str:
        """Multimodal chat via OpenAI-compat content array (data-URL images).

        Works with Budd / any OpenAI-compat server serving a VL model.
        Gate via supports_vision so the router doesn't send images to a
        text-only endpoint."""
        import base64
        import httpx
        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
        }
        async with httpx.AsyncClient(timeout=_claw_timeout_s()) as client:
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
        supports_vision: bool = True,
    ):
        self.model = model
        self._api_key = api_key
        self._client = client
        # ADR-0016 sec 5: all current Claude models are multimodal; default
        # True. Overridable for a hypothetical text-only future model.
        self.supports_vision = supports_vision

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

    async def complete_with_images(
        self, prompt: str, images: list[bytes], task_type: str = VISION_TASK
    ) -> str:
        """Multimodal message with base64 image blocks (Anthropic native).

        Screenshots are sent as image/png -- the router doesn't sniff mime
        for a single-purpose grounding path."""
        import base64
        import anthropic

        content: list[dict] = []
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(img).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": prompt})
        try:
            resp = await self._get_client().messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": content}],
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
