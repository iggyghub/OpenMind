"""Tests for cerebral.llm.subagent -- four isolation guarantees (ADR-0020 S1)."""

import asyncio

import pytest
from cerebral.llm.router import ModelRouter, ToolCall
from cerebral.llm.step_ledger import StepLedger
from cerebral.llm.subagent import (
    SubagentHandle,
    SubagentProvider,
    continuation_from,
    local_provider,
    run_subagent,
)
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


# --- H2-S3: background-job registration (ADR-0020 amendment, decision 3) --


async def test_registry_shows_running_then_done():
    """A delegation is listed as running while its execute_fn is in flight,
    and as done once run_subagent returns."""
    from cerebral.llm.subagent import job_registry

    backend = FakeBackend([ToolCall(name="echo", args={}), "final answer"])
    router = ModelRouter(backends={"fake/x": backend})
    observed = {}
    desc = "registry-running-then-done"

    async def execute(name, args):
        matches = [j for j in job_registry.list() if j.description == desc]
        observed["mid_run_status"] = matches[0].status if matches else None
        return ToolResult(content="ran", is_error=False)

    result = await run_subagent(
        desc,
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )

    assert observed["mid_run_status"] == "running"
    finished = [j for j in job_registry.list() if j.description == desc]
    assert len(finished) == 1
    assert finished[0].status == "done"
    assert result.content == "final answer"


async def test_registry_records_failed_run():
    """A sub-run that raises is recorded as failed, not silently dropped,
    and the exception still propagates to the caller (unchanged behaviour)."""
    from cerebral.llm.subagent import job_registry

    backend = FakeBackend([ToolCall(name="boom", args={})])
    router = ModelRouter(backends={"fake/x": backend})
    desc = "registry-records-failed"

    async def execute(name, args):
        raise RuntimeError("tool blew up")

    with pytest.raises(RuntimeError):
        await run_subagent(
            desc,
            router=router,
            gate_fn=_gate_allow,
            execute_fn=execute,
            all_tools=[_tool("boom")],
        )

    matches = [j for j in job_registry.list() if j.description == desc]
    assert len(matches) == 1
    assert matches[0].status == "failed"
    assert matches[0].error and "tool blew up" in matches[0].error


async def test_cancel_registered_delegation_returns_error_result():
    """Cancelling a registered job stops it and the parent still gets a
    well-formed error ToolResult -- not a raised exception, not a hang."""
    from cerebral.llm.subagent import job_registry

    backend = FakeBackend([ToolCall(name="slow", args={})])
    router = ModelRouter(backends={"fake/x": backend})
    desc = "registry-cancel-mid-flight"
    started = asyncio.Event()

    async def execute(name, args):
        started.set()
        await asyncio.sleep(10)
        return ToolResult(content="should not get here", is_error=False)

    task = asyncio.create_task(
        run_subagent(
            desc,
            router=router,
            gate_fn=_gate_allow,
            execute_fn=execute,
            all_tools=[_tool("slow")],
        )
    )

    await started.wait()
    job = next(j for j in job_registry.list() if j.description == desc)
    assert job_registry.cancel(job.id) is True

    result = await task

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert job.status == "cancelled"


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


# --- H2-S1: SubagentProvider seam (ADR-0020 amendment 2026-08-15) ---------


def test_run_subagent_satisfies_provider_protocol():
    """run_subagent already IS the fork-in-process provider -- unchanged."""
    assert isinstance(run_subagent, SubagentProvider)
    assert isinstance(local_provider, SubagentProvider)
    assert local_provider is run_subagent


async def test_provider_call_matches_direct_call():
    """Calling through the provider seam yields the same ToolResult as
    calling run_subagent directly -- the seam adds no behaviour."""

    async def execute(name, args):
        return ToolResult(content="echo result", is_error=False)

    def _kwargs():
        backend = FakeBackend([ToolCall(name="echo", args={"msg": "hi"}), "final answer"])
        return dict(
            router=ModelRouter(backends={"fake/x": backend}),
            gate_fn=_gate_allow,
            execute_fn=execute,
            all_tools=[_tool("echo")],
        )

    direct = await run_subagent("do echo hi", **_kwargs())
    via_provider = await local_provider("do echo hi", **_kwargs())

    assert via_provider.content == direct.content
    assert via_provider.is_error == direct.is_error


async def test_provider_no_nesting():
    """No-nesting guarantee holds when invoked through the provider seam too."""
    backend = FakeBackend(["done"])
    router = ModelRouter(backends={"fake/x": backend})

    all_tools = [_tool("echo"), _tool("other"), _tool("delegate")]

    async def execute(name, args):
        return ToolResult(content="x", is_error=False)

    await local_provider(
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


# --- H2-S2: continuable delegation (ADR-0020 amendment decision 2) --------


async def test_continuation_reuses_prior_context():
    """A follow-up call's transcript carries the first run's result forward."""
    backend = FakeBackend(["first result", "second result"])
    router = ModelRouter(backends={"fake/x": backend})

    async def execute(name, args):
        return ToolResult(content="x", is_error=False)

    first = await run_subagent(
        "first task",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )
    assert first.content == "first result"

    handle = continuation_from("first task", None, first)

    second = await run_subagent(
        "second task",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
        continuation=handle,
    )

    assert second.content == "second result"
    follow_up_prompt = backend.prompts[-1]
    assert "first task" in follow_up_prompt
    assert "first result" in follow_up_prompt
    assert "second task" in follow_up_prompt


async def test_omitting_continuation_runs_fresh():
    """No continuation kwarg => no leakage from a previous run's transcript."""
    backend = FakeBackend(["first result", "second result"])
    router = ModelRouter(backends={"fake/x": backend})

    async def execute(name, args):
        return ToolResult(content="x", is_error=False)

    await run_subagent(
        "first task",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )

    await run_subagent(
        "second task",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )

    follow_up_prompt = backend.prompts[-1]
    assert "first result" not in follow_up_prompt
    assert "first task" not in follow_up_prompt


async def test_continuation_still_returns_compact_result():
    """The parent still gets only the compact ToolResult on a continuation,
    never the sub-chain's intermediate steps."""
    backend = FakeBackend(
        [ToolCall(name="echo", args={"msg": "hi"}), "first result", "second result"]
    )
    router = ModelRouter(backends={"fake/x": backend})
    execute_calls: list[str] = []

    async def execute(name, args):
        execute_calls.append(name)
        return ToolResult(content="echo result", is_error=False)

    first = await run_subagent(
        "first task",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
    )
    handle = continuation_from("first task", None, first)

    second = await run_subagent(
        "second task",
        router=router,
        gate_fn=_gate_allow,
        execute_fn=execute,
        all_tools=[_tool("echo")],
        continuation=handle,
    )

    assert isinstance(second, ToolResult)
    assert second.content == "second result"
    assert not second.is_error
    assert execute_calls == ["echo"]  # only the first run's tool call fired


def test_continuation_kwarg_keeps_provider_protocol_conformance():
    """The new keyword-only param doesn't break SubagentProvider conformance."""
    assert isinstance(run_subagent, SubagentProvider)
    assert isinstance(local_provider, SubagentProvider)
    assert isinstance(SubagentHandle(transcript="t", result="r"), SubagentHandle)


async def test_parent_cancellation_propagates_not_swallowed(monkeypatch):
    """interrupt_turn (S20 #303) must still tear the turn down.

    H2-S3 wrapped chain.run in a registry Job and caught CancelledError so that
    registry.cancel() yields a well-formed ToolResult. That must NOT absorb a
    cancellation aimed at OUR OWN task -- otherwise hitting interrupt during a
    delegation leaves the parent chain running.
    """
    from cerebral.llm import subagent as subagent_mod

    class _SlowChain:
        def __init__(self, *a, **k):
            pass

        async def run(self, *a, **k):
            await asyncio.sleep(30)
            return "never"

    monkeypatch.setattr(subagent_mod, "ChainEngine", _SlowChain)
    monkeypatch.setattr(subagent_mod, "Planner", lambda *a, **k: None)
    monkeypatch.setattr(subagent_mod, "shortlist_tools", lambda task, tools: [])

    backend = FakeBackend([])
    router = ModelRouter(backends={"fake/x": backend})

    async def _execute(name, args):
        return ToolResult(content="x", is_error=False)

    task = asyncio.create_task(run_subagent(
        "t", router=router, gate_fn=_gate_allow, execute_fn=_execute, all_tools=[],
    ))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
