"""SpillStore round-trip + ChainEngine spill hook + retrieve_spilled plugin
(harness parity H5-S1 / #736). All hermetic: tmp-path DB, no model / git / net."""

import pytest

from cerebral.llm.spill_store import SpillStore
from cerebral.llm.chain_engine import ChainEngine
from cerebral.llm.router import ToolCall
from cerebral.mcp.orchestrator import ToolResult
from cerebral.security import Decision


def _store(tmp_path, threshold=10) -> SpillStore:
    return SpillStore(db_path=tmp_path / "spill.db", threshold=threshold)


# -- SpillStore unit ----------------------------------------------------------

def test_over_threshold_is_spilled_and_replaced(tmp_path):
    store = _store(tmp_path, threshold=10)
    big = "x" * 50
    assert store.should_spill(big) is True
    locator = store.spill(big)
    assert locator.startswith("spill:")
    assert store.retrieve(locator) == big  # original returned exactly


def test_under_threshold_passes_through(tmp_path):
    store = _store(tmp_path, threshold=10)
    assert store.should_spill("short") is False
    assert store.should_spill("") is False
    # Non-string content is never spilled (guards the hook against odd payloads).
    assert store.should_spill(None) is False


def test_retrieve_unknown_locator_returns_none(tmp_path):
    store = _store(tmp_path, threshold=10)
    assert store.retrieve("spill:doesnotexist") is None


def test_hint_names_locator_and_size(tmp_path):
    store = _store(tmp_path, threshold=10)
    h = store.hint("spill:abc", 1234)
    assert "spill:abc" in h and "1234" in h


# -- ChainEngine spill hook ---------------------------------------------------

class _ScriptedPlanner:
    """Returns each queued plan() result in order; finalize() is a sentinel."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    async def plan(self, transcript, tools, *, all_tools=None, prior_steps=None, error=None):
        r = self._script[self._i]
        self._i += 1
        return r

    async def finalize(self, transcript, prior_steps):
        return "finalized"


def _chain(planner, execute_fn, spill_store=None):
    async def gate(name, args):
        return Decision.SILENT

    async def record(kind, content):
        return None

    return ChainEngine(
        planner=planner,
        gate_fn=gate,
        execute_fn=execute_fn,
        record_fn=record,
        spill_store=spill_store,
    )


async def test_chain_engine_spills_big_tool_result(tmp_path):
    store = _store(tmp_path, threshold=10)
    big = "y" * 200
    captured = {}

    async def execute(name, args):
        return ToolResult(content=big, is_error=False)

    # After the big-result step, the planner's second call sees prior_steps --
    # capture what the chain fed forward as that step's result.
    class _CapturingPlanner(_ScriptedPlanner):
        async def plan(self, transcript, tools, *, all_tools=None, prior_steps=None, error=None):
            if prior_steps:
                captured["result"] = prior_steps[-1]["result"]
            return await super().plan(transcript, tools, all_tools=all_tools, prior_steps=prior_steps, error=error)

    planner = _CapturingPlanner([ToolCall(name="a", args={}), "done"])
    out = await _chain(planner, execute, spill_store=store).run("t", [])

    assert out == "done"
    forwarded = captured["result"]
    assert forwarded.startswith("[Tool output too large")   # hint, not raw text
    assert big not in forwarded                              # raw text withheld
    # The locator in the hint round-trips to the original content.
    locator = forwarded.split("stored as ", 1)[1].split(".", 1)[0].strip()
    assert store.retrieve(locator) == big


async def test_chain_engine_no_store_passes_through_raw(tmp_path):
    big = "z" * 200
    captured = {}

    async def execute(name, args):
        return ToolResult(content=big, is_error=False)

    class _CapturingPlanner(_ScriptedPlanner):
        async def plan(self, transcript, tools, *, all_tools=None, prior_steps=None, error=None):
            if prior_steps:
                captured["result"] = prior_steps[-1]["result"]
            return await super().plan(transcript, tools, all_tools=all_tools, prior_steps=prior_steps, error=error)

    planner = _CapturingPlanner([ToolCall(name="a", args={}), "done"])
    out = await _chain(planner, execute, spill_store=None).run("t", [])

    assert out == "done"
    assert captured["result"] == big  # default path unchanged -- no spilling


async def test_chain_engine_does_not_spill_small_or_error(tmp_path):
    store = _store(tmp_path, threshold=10)
    captured = []

    async def execute(name, args):
        # small success, then a big ERROR result
        if name == "small":
            return ToolResult(content="ok", is_error=False)
        return ToolResult(content="E" * 200, is_error=True)

    class _CapturingPlanner(_ScriptedPlanner):
        async def plan(self, transcript, tools, *, all_tools=None, prior_steps=None, error=None):
            if prior_steps:
                captured.append(prior_steps[-1]["result"])
            return await super().plan(transcript, tools, all_tools=all_tools, prior_steps=prior_steps, error=error)

    # small success passes through; big error stops the chain (never spilled).
    planner = _CapturingPlanner([ToolCall(name="small", args={}), ToolCall(name="boom", args={})])
    out = await _chain(planner, execute, spill_store=store).run("t", [])

    assert captured == ["ok"]          # small result untouched
    assert "E" * 200 in out            # error text surfaced raw, not a locator


# -- retrieve_spilled plugin --------------------------------------------------

async def test_retrieve_spilled_plugin_round_trip(tmp_path):
    from plugins.spill import SpillPlugin, REQUIRED_CAPABILITIES

    assert REQUIRED_CAPABILITIES == frozenset()
    store = _store(tmp_path, threshold=10)
    locator = store.spill("full text here" * 10)

    plugin = SpillPlugin(store=store)
    res = await plugin.call_tool("retrieve_spilled", {"locator": locator})
    assert res.is_error is False
    assert res.content == "full text here" * 10

    miss = await plugin.call_tool("retrieve_spilled", {"locator": "spill:nope"})
    assert miss.is_error is True

    blank = await plugin.call_tool("retrieve_spilled", {"locator": ""})
    assert blank.is_error is True
