from cerebral.llm.router import ModelRouter, ToolCall
from cerebral.eval import Case, Result, run_cases

class FakeBackend:
    supports_vision: bool = False

    def __init__(self, reply: str = "", tool: ToolCall | str = None):
        self._reply = reply
        self._tool = tool

    async def complete(self, prompt: str, task_type: str) -> str:
        return self._reply

    async def complete_with_tools(self, prompt: str, tools: list[dict]) -> ToolCall | str:
        return self._tool

    async def complete_with_images(self, prompt, images, task_type) -> str:
        return ""

async def test_response_match():
    backend = FakeBackend(reply="hello")
    router = ModelRouter(backends={"fake/x": backend})
    cases = [Case(name="t1", prompt="hi", expected={"response": "hello"})]
    results = await run_cases(router, cases)
    assert len(results) == 1
    res = results[0]
    assert res.passed is True
    assert res.detail == ""

async def test_response_mismatch():
    backend = FakeBackend(reply="hello")
    router = ModelRouter(backends={"fake/x": backend})
    cases = [Case(name="t2", prompt="hi", expected={"response": "goodbye"})]
    results = await run_cases(router, cases)
    assert len(results) == 1
    res = results[0]
    assert res.passed is False
    assert "goodbye" in res.detail

async def test_tool_trajectory_match():
    backend = FakeBackend(tool=ToolCall(name="search", args={"q": "cats"}))
    router = ModelRouter(backends={"fake/x": backend})
    cases = [Case(name="t3", prompt="find cats", expected={"tool": "search", "tool_args": {"q": "cats"}})]
    results = await run_cases(router, cases)
    assert len(results) == 1
    res = results[0]
    assert res.passed is True
    assert res.detail == ""
