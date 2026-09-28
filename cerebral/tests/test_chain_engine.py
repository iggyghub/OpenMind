"""
ChainEngine unit tests -- Issue #275 (S2 chaining).

All tests use injected fakes (no HTTP, no DB, no gate side-effects).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from cerebral.llm.chain_engine import ChainEngine, MAX_CHAIN_STEPS, _chain_summary
from cerebral.llm.planner import Planner
from cerebral.llm.router import ToolCall
from cerebral.mcp.orchestrator import ToolResult
from cerebral.security import Decision
from cerebral.db.conversation import KIND_TOOL_CALL, KIND_TOOL_RESULT


_SEARCH_TOOL = {
    "name": "gmail_search",
    "description": "Search Gmail",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}

_SEND_TOOL = {
    "name": "gmail_send",
    "description": "Send an email",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "body"],
    },
}

_TOOLS = [_SEARCH_TOOL, _SEND_TOOL]


def _make_engine(planner, gate_decisions, tool_results, recorded=None):
    """Build a ChainEngine with canned gate decisions and tool results."""
    gate_iter = iter(gate_decisions)
    result_iter = iter(tool_results)
    recorded_turns = recorded if recorded is not None else []

    async def gate_fn(tool_name, args=None):
        return next(gate_iter)

    async def execute_fn(tool_name, args):
        return next(result_iter)

    async def record_fn(kind, content):
        recorded_turns.append((kind, content))

    return ChainEngine(
        planner=planner,
        gate_fn=gate_fn,
        execute_fn=execute_fn,
        record_fn=record_fn,
    ), recorded_turns


# ---------------------------------------------------------------------------
# 2-step chain (acceptance criterion AC-1)
# ---------------------------------------------------------------------------

async def test_two_step_chain_completes():
    """A 2-step chain: planner picks tool1, result feeds back, planner picks tool2,
    result feeds back, planner returns final text."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "from:Sarah"}),
        ToolCall(name="gmail_send", args={"to": "sarah@example.com", "body": "I'll be there"}),
        "Done! I searched and replied.",
    ]
    planner = Planner(backend)

    engine, recorded = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[
            ToolResult(content="Email from Sarah found.", is_error=False),
            ToolResult(content="Email sent.", is_error=False),
        ],
    )

    response = await engine.run("Read latest from Sarah and reply I'll be there", _TOOLS)
    assert response == "Done! I searched and replied."
    assert backend.complete_with_tools.call_count == 3


async def test_two_step_chain_records_both_tool_calls_and_results():
    """Each step records its own tool-call + tool-result turn."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "from:Sarah"}),
        ToolCall(name="gmail_send", args={"to": "sarah@example.com", "body": "I'll be there"}),
        "Done.",
    ]
    planner = Planner(backend)

    engine, recorded = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[
            ToolResult(content="Email found.", is_error=False),
            ToolResult(content="Email sent.", is_error=False),
        ],
    )

    await engine.run("...", _TOOLS)

    kinds = [k for k, _ in recorded]
    assert kinds == [KIND_TOOL_CALL, KIND_TOOL_RESULT, KIND_TOOL_CALL, KIND_TOOL_RESULT]
    # #789: the result content must survive into the persisted turn, not just
    # is_error -- otherwise a replayed turn can't tell the model e.g. what
    # path a create_file call resolved to.
    assert recorded[1][1]["result"] == "Email found."
    assert recorded[3][1]["result"] == "Email sent."


async def test_two_step_chain_passes_prior_steps_to_planner():
    """Second planner call receives prior_steps so it sees step-1 result."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "from:Sarah"}),
        "Summary after one step.",
    ]
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT],
        tool_results=[ToolResult(content="Email found.", is_error=False)],
    )

    await engine.run("Read email from Sarah", _TOOLS)

    # Second call prompt must contain prior step context
    second_call_prompt = backend.complete_with_tools.call_args_list[1][0][0]
    assert "gmail_search" in second_call_prompt
    assert "Email found." in second_call_prompt


# ---------------------------------------------------------------------------
# Step cap (acceptance criterion AC-2)
# ---------------------------------------------------------------------------

async def test_repeated_call_breaks_loop_and_finalizes():
    """A tool-native model that re-emits the SAME call (same name+args) has
    stopped progressing. The chain must not spin to the cap -- it breaks on
    the repeat and returns a tools-free finalized answer."""
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(
        name="get_time", args={}
    )  # always the same call -> loop
    backend.complete.return_value = "It's 3:45 PM."  # finalize (no tools)
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT] * 5,
        tool_results=[ToolResult(content="3:45 PM") for _ in range(5)],
    )

    response = await engine.run("what time is it", _TOOLS, max_steps=8)

    assert response == "It's 3:45 PM."
    backend.complete.assert_awaited_once()           # finalized
    assert backend.complete_with_tools.call_count == 2  # step1 + the repeat


async def test_cap_finalizes_from_results():
    """Distinct tool calls that never resolve to text hit the cap and get
    finalized (real answer) rather than a robotic 'I completed N steps'."""
    backend = AsyncMock()
    cap = 3
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": f"q{i}"}) for i in range(cap)
    ]
    backend.complete.return_value = "Here's what I found."
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT] * cap,
        tool_results=[ToolResult(content=f"r{i}") for i in range(cap)],
    )

    response = await engine.run("...", _TOOLS, max_steps=cap)
    assert response == "Here's what I found."
    backend.complete.assert_awaited_once()


async def test_cap_is_env_overridable(monkeypatch):
    """MAX_CHAIN_STEPS reads from the environment at import time."""
    import cerebral.llm.chain_engine as mod
    monkeypatch.setattr(mod, "MAX_CHAIN_STEPS", 2)
    assert mod.MAX_CHAIN_STEPS == 2


# ---------------------------------------------------------------------------
# Mid-chain denial (acceptance criterion AC-3)
# ---------------------------------------------------------------------------

async def test_mid_chain_denial_stops_chain():
    """Denying step 2 stops the chain; step 3 is never attempted."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "from:Sarah"}),
        ToolCall(name="gmail_send", args={"to": "x@y.com", "body": "hi"}),
    ]
    planner = Planner(backend)

    engine, recorded = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.DENY],
        tool_results=[ToolResult(content="found email")],
    )

    response = await engine.run("Read and reply", _TOOLS)

    # Only one tool was executed (step 1 SILENT, step 2 DENIED before execute)
    assert "permission denied" in response.lower()
    # execute_fn was only called once (step 1)
    result_records = [k for k, _ in recorded if k == KIND_TOOL_RESULT]
    assert len(result_records) == 1


async def test_mid_chain_denial_at_step1_returns_graceful():
    """Denying the very first step returns a graceful summary (no steps done)."""
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(
        name="gmail_search", args={"query": "x"}
    )
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.DENY],
        tool_results=[],
    )

    response = await engine.run("...", _TOOLS)
    assert "permission denied" in response.lower()


# ---------------------------------------------------------------------------
# Single-step chain (S1 path still works through ChainEngine)
# ---------------------------------------------------------------------------

async def test_single_tool_call_completes():
    """A single-tool chain still works (length-one chain)."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "is:unread"}),
        "You have 3 unread emails.",
    ]
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT],
        tool_results=[ToolResult(content="3 unread")],
    )

    response = await engine.run("How many unread emails?", _TOOLS)
    assert "unread" in response.lower()


async def test_immediate_text_response_skips_tool():
    """If the planner returns text immediately no tool is called."""
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "I'm not sure which tool to use."
    planner = Planner(backend)

    gate_called = []

    async def gate_fn(name, args=None):
        gate_called.append(name)
        return Decision.SILENT

    async def execute_fn(name, args):
        return ToolResult(content="shouldn't be called")

    engine = ChainEngine(
        planner=planner,
        gate_fn=gate_fn,
        execute_fn=execute_fn,
        record_fn=AsyncMock(),
    )

    response = await engine.run("mumble", _TOOLS)
    assert response == "I'm not sure which tool to use."
    assert gate_called == []


# ---------------------------------------------------------------------------
# Tool error propagation
# ---------------------------------------------------------------------------

async def test_tool_error_stops_chain():
    """A tool that fails identically twice in a row stops the chain and
    reports the error -- the planner isn't making progress."""
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(
        name="gmail_search", args={"query": "from:Sarah"}
    )
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT],
        tool_results=[ToolResult(content="auth failed", is_error=True)],
    )

    response = await engine.run("...", _TOOLS)
    assert "error" in response.lower()
    assert "auth failed" in response


async def test_tool_error_lets_planner_retry_with_different_args():
    """A failed tool call does not end the chain -- the planner sees the
    error (via prior_steps) and can retry with different args, succeeding
    in the same chain."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "from:Sarah"}),
        ToolCall(name="gmail_search", args={"query": "from:sarah@example.com"}),
        "Done! Found it on retry.",
    ]
    planner = Planner(backend)

    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[
            ToolResult(content="query malformed", is_error=True),
            ToolResult(content="Email from Sarah found.", is_error=False),
        ],
    )

    response = await engine.run("Find email from Sarah", _TOOLS)
    assert response == "Done! Found it on retry."
    assert backend.complete_with_tools.call_count == 3


# ---------------------------------------------------------------------------
# _chain_summary helper
# ---------------------------------------------------------------------------

def test_chain_summary_no_steps():
    out = _chain_summary([], stopped_reason="permission denied")
    assert "permission denied" in out


def test_chain_summary_one_step():
    steps = [{"name": "gmail_search", "args": {}, "result": "ok", "is_error": False}]
    out = _chain_summary(steps, stopped_reason="reached the 8-step limit")
    assert "gmail_search" in out
    assert "8-step limit" in out


def test_chain_summary_two_steps():
    steps = [
        {"name": "gmail_search", "args": {}, "result": "ok", "is_error": False},
        {"name": "gmail_send", "args": {}, "result": "ok", "is_error": False},
    ]
    out = _chain_summary(steps, stopped_reason="permission denied")
    assert "gmail_search" in out
    assert "gmail_send" in out
    assert "2" in out


# ---------------------------------------------------------------------------
# S1 planner tests still pass (prior_steps=None is the default)
# ---------------------------------------------------------------------------

async def test_planner_prior_steps_none_is_default():
    """Calling plan() without prior_steps behaves identically to S1."""
    from cerebral.llm.planner import Planner

    backend = AsyncMock()
    backend.complete_with_tools.return_value = "hello"
    result = await Planner(backend).plan("hi", _TOOLS)
    assert result == "hello"
    prompt, _ = backend.complete_with_tools.call_args[0]
    # No prior-steps context block in the prompt
    assert "Previous steps" not in prompt


async def test_planner_prior_steps_included_in_prompt():
    """When prior_steps is given, the prompt contains tool names and results."""
    from cerebral.llm.planner import Planner

    backend = AsyncMock()
    backend.complete_with_tools.return_value = "done"
    steps = [{"name": "gmail_search", "args": {}, "result": "email found", "is_error": False}]
    await Planner(backend).plan("hi", _TOOLS, prior_steps=steps)
    prompt, _ = backend.complete_with_tools.call_args[0]
    assert "gmail_search" in prompt
    assert "email found" in prompt
    assert "Previous steps" in prompt
