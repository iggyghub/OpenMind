"""Tests for per-call token usage tracking in ModelRouter."""

import pytest

from cerebral.llm.router import ModelRouter


class FakeBackend:
    """Minimal backend for testing router usage tracking."""
    supports_vision = False

    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self._last_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        self._last_usage = {
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
        }
        return "ok"

    async def complete_with_tools(self, prompt: str, tools: list[dict]):
        raise NotImplementedError

    async def complete_with_images(self, prompt: str, images: list[bytes], task_type: str = "chat"):
        raise NotImplementedError


async def test_router_usage_totals():
    backend_a = FakeBackend(prompt_tokens=100, completion_tokens=50)
    backend_b = FakeBackend(prompt_tokens=200, completion_tokens=100)
    backends = {"model_a": backend_a, "model_b": backend_b}
    models = {
        "model_a": {"label": "Model A", "is_cloud": False},
        "model_b": {"label": "Model B", "is_cloud": False},
    }
    router = ModelRouter(backends=backends, models=models, default_model="model_a")

    # First call uses default (model_a)
    await router.complete("hello", "chat")
    # Second call switches to model_b and calls it
    router.switch_model("model_b")
    await router.complete("world", "chat")

    totals = router.usage_totals()
    assert totals == {
        "model_a": {"prompt_tokens": 100, "completion_tokens": 50},
        "model_b": {"prompt_tokens": 200, "completion_tokens": 100},
    }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
