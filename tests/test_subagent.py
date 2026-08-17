"""Tests for cerebral.llm.subagent -- four isolation guarantees (ADR-0020 S1)."""

import pytest
from cerebral.llm.router import ModelRouter, ToolCall
from cerebral.llm.step_ledger import StepLedger
from cerebral.llm.subagent import run_subagent
from cerebral.mcp.orchestrator import ToolResult
from cerebral.security import Decision


class FakeBackend:
    """Scripted backend; records every complete_with_tools call for assertions."""

    supports_vision = False

    def __init__(self, script: list):
        self._script = list(script)
        self._i = 0
        self.offered_tools: list[list[dict]] = []
        self.prompts: list[str] = []

    async def complete(self, prompt: str, task_type: str) -> str:
        r = self._script[self._i]
        self._i += 1
        return r if isinstance(r, str) else ""

    async def complete_with_tools(self, prompt: str, tools: list[dict]) -> ToolCall | str:
        self.offered_tools.append(list(tools))
        self.prompts.append(prompt)
        r = self._script[self._i]
        self._i += 1
        return r

    async def complete_with_images(self, prompt, images, task_type) -> str:
        return ""


def _tool(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}


async def _gate_allow(name, args):
    return Decision.SILENT


async def test_compact_return():
    """1. Compact return: parent sees only the final text, not intermediate steps."""
    backend = FakeBackend([ToolCall(name="echo", args={"msg": "hi"}), "final answer"])
    router = ModelRouter(backends={"fake/x": backend})
    execute_calls: list[str] = []

    async def execute(name, args):
        execute_calls.append(name)
        return ToolResult(content="echo result", is_error=False)

    result = await run_subagent(
        "do echo hi",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )

    assert isinstance(result, ToolResult)
    assert result.content == "final answer"
    assert not result.is_error
    assert "echo" in execute_calls


async def test_tool_scoping():
    """2. Tool scoping: backend sees only the named subset, not the full registry."""
    backend = FakeBackend(["done"])
    router = ModelRouter(backends={"fake/x": backend})

    all_tools = [_tool("echo"), _tool("other"), _tool("delegate")]

    async def execute(name, args):
        return ToolResult(content="x", is_error=False)

    await run_subagent(
        "echo something",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=all_tools,
        tools=["echo"],
    )

    assert len(backend.offered_tools) == 1
    offered_names = [t["name"] for t in backend.offered_tools[0]]
    assert offered_names == ["echo"]


async def test_no_nesting():
    """3. No nesting: delegate is never offered to the backend, even via shortlist."""
    backend = FakeBackend(["done"])
    router = ModelRouter(backends={"fake/x": backend})

    all_tools = [_tool("echo"), _tool("other"), _tool("delegate")]

    async def execute(name, args):
        return ToolResult(content="x", is_error=False)

    await run_subagent(
        "do something",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=all_tools,
        tools=None,  # shortlist path
    )

    for call_tools in backend.offered_tools:
        offered_names = {t["name"] for t in call_tools}
        assert "delegate" not in offered_names


async def test_gate_reuse():
    """4. Gate reuse: a denying gate stops the chain; execute_fn is never called."""
    backend = FakeBackend([ToolCall(name="echo", args={})])
    router = ModelRouter(backends={"fake/x": backend})
    execute_calls: list[str] = []

    async def execute(name, args):
        execute_calls.append(name)
        return ToolResult(content="should not get here", is_error=False)

    async def gate_deny(name, args):
        return Decision.ASK  # non-SILENT => chain stops

    result = await run_subagent(
        "echo hi",
        router=router,
        gate_fn=gate_deny,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )

    assert execute_calls == []
    assert "permission denied" in result.content


async def test_resume_skips_completed_step(tmp_path):
    """5. Resume passthrough: a step already in the ledger is not re-executed."""
    led = StepLedger(db_path=tmp_path / "ledger.db")
    # Pretend "echo" already ran and was persisted before a crash.
    led.record(
        "runX", "echo:{}", {"name": "echo", "args": {}, "result": "seeded", "is_error": False}
    )

    backend = FakeBackend(
        [ToolCall(name="echo", args={}), ToolCall(name="step2", args={}), "final answer"]
    )
    router = ModelRouter(backends={"fake/x": backend})
    execute_calls: list[str] = []

    async def execute(name, args):
        execute_calls.append(name)
        return ToolResult(content="ran", is_error=False)

    result = await run_subagent(
        "do echo then step2",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo"), _tool("step2")],
        run_id="runX",
        ledger=led,
    )

    assert execute_calls == ["step2"]  # echo was replayed from the ledger
    assert result.content == "final answer"


async def test_no_run_id_executes_every_step():
    """6. Control: with run_id/ledger omitted, every scripted step is executed."""
    backend = FakeBackend(
        [ToolCall(name="echo", args={}), ToolCall(name="step2", args={}), "final answer"]
    )
    router = ModelRouter(backends={"fake/x": backend})
    execute_calls: list[str] = []

    async def execute(name, args):
        execute_calls.append(name)
        return ToolResult(content="ran", is_error=False)

    result = await run_subagent(
        "do echo then step2",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo"), _tool("step2")],
    )

    assert execute_calls == ["echo", "step2"]
    assert result.content == "final answer"


async def test_run_id_and_ledger_passed_through(monkeypatch):
    """7. Pass-through: chain.run actually receives run_id and ledger."""
    from cerebral.llm.chain_engine import ChainEngine

    captured = {}
    orig_run = ChainEngine.run

    async def spy_run(self, transcript, tools, **kwargs):
        captured.update(kwargs)
        return "final answer"

    monkeypatch.setattr(ChainEngine, "run", spy_run)

    backend = FakeBackend(["unused"])
    router = ModelRouter(backends={"fake/x": backend})
    led = StepLedger(db_path=":memory:")

    async def execute(name, args):
        return ToolResult(content="ran", is_error=False)

    await run_subagent(
        "do echo",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
        run_id="runY",
        ledger=led,
    )

    assert captured.get("run_id") == "runY"
    assert captured.get("ledger") is led
