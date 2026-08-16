"""_video_verify routes its research through run_subagent -- ADR-0020 S2 / #728.

No real model, no network, no real Cerebral: run_subagent and _router.complete
are both monkeypatched fakes. asyncio_mode=auto in this suite -- test bodies
are plain `async def`, never `asyncio.run` (that closes the shared loop).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cerebral.main as main  # noqa: E402
from cerebral.mcp.orchestrator import ToolResult  # noqa: E402

_VERDICT_JSON = '{"verdict": "plausible", "confidence": 0.7, "evidence": ["e1"]}'
_IDEA_TEXT = "Sell trending products"


async def test_video_verify_delegates_with_scoped_args(monkeypatch):
    recorded = {}

    async def fake_run_subagent(task, **kwargs):
        recorded["task"] = task
        recorded.update(kwargs)
        return ToolResult(content="EVIDENCE-DIGEST", is_error=False)

    async def fake_complete(prompt, task_type="chat"):
        return _VERDICT_JSON

    monkeypatch.setattr(main, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(main._router, "complete", fake_complete)

    await main._video_verify("dropshipping", _IDEA_TEXT)

    assert recorded["tools"] == ["web_search"]
    assert recorded["gate_fn"] is main._gate_tool
    # bound methods aren't `is`-identical across attribute accesses in CPython
    # (a fresh bound-method object is created each time); == compares the
    # same (function, instance) pair, which is the real invariant here.
    assert recorded["execute_fn"] == main._orc.call_tool
    assert recorded["router"] is main._router
    # tools_for_llm is a @property that builds a fresh list each access, so
    # (like execute_fn above) equality is the achievable invariant, not `is`.
    assert recorded["all_tools"] == main._orc.tools_for_llm
    assert _IDEA_TEXT in recorded["task"]


async def test_video_verify_consumes_only_content(monkeypatch):
    async def fake_run_subagent(task, **kwargs):
        return ToolResult(content="EVIDENCE-DIGEST", is_error=False)

    prompts = []

    async def fake_complete(prompt, task_type="chat"):
        prompts.append(prompt)
        return _VERDICT_JSON

    monkeypatch.setattr(main, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(main._router, "complete", fake_complete)

    result = await main._video_verify("dropshipping", _IDEA_TEXT)

    assert "EVIDENCE-DIGEST" in prompts[0]
    assert result["verdict"] == "plausible"


async def test_video_verify_degrades_when_subagent_raises(monkeypatch):
    async def fake_run_subagent(task, **kwargs):
        raise RuntimeError("sub-agent unavailable")

    prompts = []

    async def fake_complete(prompt, task_type="chat"):
        prompts.append(prompt)
        return _VERDICT_JSON

    monkeypatch.setattr(main, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(main._router, "complete", fake_complete)

    result = await main._video_verify("dropshipping", _IDEA_TEXT)

    assert "EVIDENCE-DIGEST" not in prompts[0]
    assert result == {"verdict": "plausible", "confidence": 0.7, "evidence": ["e1"]}


async def test_video_verify_ignores_an_errored_result(monkeypatch):
    async def fake_run_subagent(task, **kwargs):
        return ToolResult(content="junk", is_error=True)

    prompts = []

    async def fake_complete(prompt, task_type="chat"):
        prompts.append(prompt)
        return _VERDICT_JSON

    monkeypatch.setattr(main, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(main._router, "complete", fake_complete)

    await main._video_verify("dropshipping", _IDEA_TEXT)

    assert "junk" not in prompts[0]


async def test_gate_stays_module_level():
    """Guards against a refactor pushing the gate back inside the turn closure."""
    assert inspect.iscoroutinefunction(main._gate_tool) is True
