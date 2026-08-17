"""
Delegation eval cases -- ADR-0020 S4 (Issue #730), the eval harness (B) gate
for planner autonomy.

`delegate` is new: the planner can now choose, on its own, to spin off a
sub-agent instead of calling a tool directly (ADR-0020 decision 3 lifts the
prior "caller-invoked only" restriction). ADR-0020's own note that "the 8B
already over-emits and mis-args" is exactly the failure mode these cases
probe: does the planner scope a delegation with a focused `tools`
allow-list when the task warrants one (decision 8), and does it leave
`delegate` alone for a task one direct tool call already covers?

Same Case/expected shape as tests/test_eval_harness.py's "tool" cases --
run through `cerebral.eval.run_cases(router, CASES)` against any backend: a
scripted FakeBackend in cerebral/tests/test_eval_cases_delegate.py, or a
real model for a live eval run.
"""
from cerebral.eval import Case

CASES: list[Case] = [
    Case(
        name="delegate_scoped_for_heavy_research_subtask",
        prompt=(
            "Research the founding history of Anthropic and give me a "
            "two-sentence summary."
        ),
        expected={
            "tool": "delegate",
            "tool_args": {
                "task": (
                    "Research the founding history of Anthropic and give "
                    "me a two-sentence summary."
                ),
                "tools": ["web_search"],
            },
        },
    ),
    Case(
        name="delegate_not_used_for_single_direct_tool_task",
        prompt="What time is it right now?",
        expected={"tool": "get_time", "tool_args": {}},
    ),
]
