"""
Hermetic proof that the delegation eval cases (cerebral/eval/cases_delegate.py)
actually detect a correctly- vs incorrectly-scoped delegation trajectory --
ADR-0020 S4 (Issue #730). No real model: a scripted backend stands in for
the planner, mirroring tests/test_eval_harness.py.
"""
from cerebral.eval import run_cases
from cerebral.eval.cases_delegate import CASES
from cerebral.llm.router import ModelRouter, ToolCall


class ScriptedBackend:
    """Always returns the same pre-scripted ToolCall/str -- a fake planner."""

    supports_vision = False

    def __init__(self, reply):
        self._reply = reply

    async def complete(self, prompt, task_type):
        return self._reply if isinstance(self._reply, str) else ""

    async def complete_with_tools(self, prompt, tools):
        return self._reply

    async def complete_with_images(self, prompt, images, task_type):
        return ""


def _case(name: str):
    return next(c for c in CASES if c.name == name)


async def test_correctly_scoped_delegation_passes():
    """A planner that reaches for delegate with the RIGHT scoped tools list
    passes the research case (ADR-0020 decision 8: focused allow-list)."""
    backend = ScriptedBackend(
        ToolCall(
            name="delegate",
            args={
                "task": (
                    "Research the founding history of Anthropic and give "
                    "me a two-sentence summary."
                ),
                "tools": ["web_search"],
            },
        )
    )
    router = ModelRouter(backends={"fake/x": backend})
    results = await run_cases(
        router, [_case("delegate_scoped_for_heavy_research_subtask")]
    )
    assert results[0].passed is True


async def test_unscoped_delegation_fails_the_case():
    """A planner that delegates WITHOUT scoping tools (over-emits against the
    full registry) does not match the scoped-tools expectation -- this is
    the over-emission failure mode ADR-0020 flags for the 8B."""
    backend = ScriptedBackend(
        ToolCall(
            name="delegate",
            args={
                "task": (
                    "Research the founding history of Anthropic and give "
                    "me a two-sentence summary."
                ),
                "tools": None,
            },
        )
    )
    router = ModelRouter(backends={"fake/x": backend})
    results = await run_cases(
        router, [_case("delegate_scoped_for_heavy_research_subtask")]
    )
    assert results[0].passed is False


async def test_direct_tool_task_does_not_over_delegate():
    """A planner that reaches for the direct tool (not delegate) on a
    trivial single-tool task passes the negative case."""
    backend = ScriptedBackend(ToolCall(name="get_time", args={}))
    router = ModelRouter(backends={"fake/x": backend})
    results = await run_cases(
        router, [_case("delegate_not_used_for_single_direct_tool_task")]
    )
    assert results[0].passed is True


async def test_delegate_used_when_direct_tool_expected_fails_the_case():
    """A planner that over-delegates a trivial task (reaches for delegate
    instead of the direct tool) fails the negative case."""
    backend = ScriptedBackend(
        ToolCall(name="delegate", args={"task": "What time is it right now?"})
    )
    router = ModelRouter(backends={"fake/x": backend})
    results = await run_cases(
        router, [_case("delegate_not_used_for_single_direct_tool_task")]
    )
    assert results[0].passed is False
