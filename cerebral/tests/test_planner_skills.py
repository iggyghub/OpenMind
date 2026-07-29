"""
Planner skills-wiring tests -- S4 (Issue #539, ADR-0014).

Covers: system prompt mentions skill_list/skill_use; explicit "/name" and NL
"use the X skill" invocation resolves to a skill_use ToolCall without a
backend round-trip; unknown names are forwarded rather than guessed at (the
"clear message, no crash" guarantee lives in plugins/skills.py's skill_use,
already covered by test_plugin_skills.py::test_skill_use_unknown_name_errors).
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cerebral.llm.planner import Planner, _SYSTEM_PROMPT, resolve_skill_invocation
from cerebral.llm.router import ToolCall

_FAKE_TOOLS = [
    {
        "name": "get_time",
        "description": "Get the current time",
        "input_schema": {"type": "object", "properties": {}},
    }
]


# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------

def test_system_prompt_mentions_skills():
    assert "skill_list" in _SYSTEM_PROMPT
    assert "skill_use" in _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# resolve_skill_invocation -- pure mapping
# ---------------------------------------------------------------------------

def test_resolve_slash_name_happy():
    call = resolve_skill_invocation("/deploy")
    assert call == ToolCall(name="skill_use", args={"name": "deploy"})


def test_resolve_slash_name_strips_whitespace():
    call = resolve_skill_invocation("  /deploy  ")
    assert call == ToolCall(name="skill_use", args={"name": "deploy"})


def test_resolve_nl_use_the_x_skill():
    call = resolve_skill_invocation("please use the deploy skill now")
    assert call == ToolCall(name="skill_use", args={"name": "deploy"})


def test_resolve_unknown_name_still_forwards_no_crash():
    # planner doesn't validate names -- skill_use itself fails soft (S1).
    call = resolve_skill_invocation("/does-not-exist")
    assert call == ToolCall(name="skill_use", args={"name": "does-not-exist"})


def test_resolve_plain_text_returns_none():
    assert resolve_skill_invocation("what time is it in Tokyo?") is None


def test_resolve_slash_with_trailing_words_returns_none():
    # not a bare "/name" -- falls through to the normal LLM planning path.
    assert resolve_skill_invocation("/deploy the app to staging") is None


# ---------------------------------------------------------------------------
# Planner.plan() -- explicit invocation bypasses the backend
# ---------------------------------------------------------------------------

async def test_plan_slash_name_bypasses_backend():
    backend = AsyncMock()
    result = await Planner(backend).plan("/deploy", _FAKE_TOOLS)
    assert result == ToolCall(name="skill_use", args={"name": "deploy"})
    backend.complete_with_tools.assert_not_called()


async def test_plan_nl_use_skill_bypasses_backend():
    backend = AsyncMock()
    result = await Planner(backend).plan("use the deploy skill", _FAKE_TOOLS)
    assert result == ToolCall(name="skill_use", args={"name": "deploy"})
    backend.complete_with_tools.assert_not_called()


async def test_plan_unknown_skill_name_still_bypasses_no_crash():
    backend = AsyncMock()
    result = await Planner(backend).plan("/nope", _FAKE_TOOLS)
    assert result == ToolCall(name="skill_use", args={"name": "nope"})
    backend.complete_with_tools.assert_not_called()


async def test_plan_mid_chain_does_not_reinterpret_slash_transcript():
    # prior_steps set -> this is a chain continuation, not a fresh explicit
    # invocation, so the LLM decides (transcript is the original request).
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "done"
    result = await Planner(backend).plan(
        "/deploy", _FAKE_TOOLS, prior_steps=[{"name": "skill_use", "args": {}, "result": "ok", "is_error": False}]
    )
    assert result == "done"
    backend.complete_with_tools.assert_awaited_once()


async def test_plan_normal_text_still_calls_backend():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(name="get_time", args={})
    result = await Planner(backend).plan("what time is it?", _FAKE_TOOLS)
    assert result == ToolCall(name="get_time", args={})
    backend.complete_with_tools.assert_awaited_once()
