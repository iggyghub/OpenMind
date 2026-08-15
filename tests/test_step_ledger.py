"""StepLedger round-trip + ChainEngine crash-resume (harness improvement C)."""

from cerebral.llm.step_ledger import StepLedger
from cerebral.llm.chain_engine import ChainEngine
from cerebral.llm.router import ToolCall
from cerebral.mcp.orchestrator import ToolResult
from cerebral.security import Decision


def _ledger(tmp_path) -> StepLedger:
    return StepLedger(db_path=tmp_path / "ledger.db")


def test_record_completed_clear_round_trip(tmp_path):
    led = _ledger(tmp_path)
    led.record("run1", "a:{}", {"name": "a", "args": {"x": 1}, "result": "ra", "is_error": False})
    led.record("run1", "b:{}", {"name": "b", "args": {}, "result": "rb", "is_error": False})
    # A different run is isolated.
    led.record("run2", "c:{}", {"name": "c", "args": {}, "result": "rc", "is_error": False})

    done = led.completed("run1")
    assert [s["name"] for s in done] == ["a", "b"]  # execution order preserved
    assert done[0]["args"] == {"x": 1}
    assert done[0]["result"] == "ra"
    assert led.completed("run2")[0]["name"] == "c"

    assert led.clear("run1") == 2
    assert led.completed("run1") == []
    assert led.completed("run2")[0]["name"] == "c"  # untouched


class _ScriptedPlanner:
    """Returns each queued plan() result in order; finalize() is a sentinel."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    async def plan(self, transcript, tools, *, prior_steps=None, error=None):
        r = self._script[self._i]
        self._i += 1
        return r

    async def finalize(self, transcript, prior_steps):
        return "finalized"


def _chain(planner, execute_fn):
    async def gate(name, args):
        return Decision.SILENT

    async def record(kind, content):
        return None

    return ChainEngine(planner=planner, gate_fn=gate, execute_fn=execute_fn, record_fn=record)


async def test_resume_skips_completed_step(tmp_path):
    led = _ledger(tmp_path)
    # Pretend step A already ran and was persisted before a crash.
    led.record("runX", 'a:{}', {"name": "a", "args": {}, "result": "ra", "is_error": False})

    calls: list[str] = []

    async def execute(name, args):
        calls.append(name)
        return ToolResult(content=f"r{name}", is_error=False)

    planner = _ScriptedPlanner([ToolCall(name="a", args={}), ToolCall(name="b", args={}), "done"])
    chain = _chain(planner, execute)

    out = await chain.run("t", [], run_id="runX", ledger=led)

    assert out == "done"
    assert calls == ["b"]  # A was replayed from the ledger, only B executed
    assert led.completed("runX") == []  # cleared on clean finish


async def test_no_ledger_executes_every_step(tmp_path):
    # Same script, but no ledger/run_id => nothing is skipped.
    calls: list[str] = []

    async def execute(name, args):
        calls.append(name)
        return ToolResult(content=f"r{name}", is_error=False)

    planner = _ScriptedPlanner([ToolCall(name="a", args={}), ToolCall(name="b", args={}), "done"])
    chain = _chain(planner, execute)

    out = await chain.run("t", [])

    assert out == "done"
    assert calls == ["a", "b"]
