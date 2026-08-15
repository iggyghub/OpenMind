"""Tests for ModelRouter per-call token usage tracking."""

import unittest
from cerebral.llm.router import ModelRouter


class FakeBackend:
    """Minimal backend that injects known token usage on each complete() call."""
    supports_vision = False

    async def complete(self, prompt: str, task_type: str) -> str:
        self._last_usage = {"prompt_tokens": 100, "completion_tokens": 200}
        return "ok"

    async def complete_with_tools(self, prompt: str, tools: list) -> str:
        return "ok"

    async def complete_with_images(self, prompt: str, images: list, task_type: str) -> str:
        return "ok"


class TestRouterUsage(unittest.IsolatedAsyncioTestCase):
    async def test_usage_totals(self):
        backend = FakeBackend()
        router = ModelRouter(backends={"fake/model": backend})

        # First call
        await router.complete("prompt1", "chat")
        # Second call
        await router.complete("prompt2", "chat")

        totals = router.usage_totals()
        self.assertEqual(totals["fake/model"]["prompt_tokens"], 200)
        self.assertEqual(totals["fake/model"]["completion_tokens"], 400)


if __name__ == "__main__":
    unittest.main()
