"""
Delegate plugin tests -- ADR-0020 S4 (Issue #730), the planner-autonomy
slice. Fully hermetic: no real model, no real orchestrator gate/executor --
everything the plugin needs is injected via set_subagent_context.

Covers:
  - the plugin registers and "delegate" appears in the orchestrator's tool list
  - delegate() calls run_subagent with the scoped tools passed through, and
    returns exactly its .content (compact result, ADR-0020 decision 9)
  - the unwired seam fails closed with an error ToolResult
  - the sub-agent cannot nest -- "delegate" is never offered to the sub-planner
"""
import pytest

import plugins.delegate as delegate_mod
from cerebral.llm.router import ModelRouter, ToolCall
from cerebral.mcp.orchestrator import MCPOrchestrator, ToolResult
from cerebral.security import Decision
from plugins.delegate import DelegatePlugin, create


@pytest.fixture(autouse=True)
def _reset_context_fn():
    """The subagent context is a module global (matches how cerebral.main's
    seam wires it -- Issue #153). Reset around each test so one test's
    wiring doesn't leak into the next."""
    delegate_mod.set_subagent_context(None)
    yield
    delegate_mod.set_subagent_context(None)


class FakeBackend:
    """Scripted sub-planner backend; records every offered tool list."""

    supports_vision = False

    def __init__(self, script: list):
        self._script = list(script)
        self._i = 0
        self.offered_tools: list[list[dict]] = []

    async def complete(self, prompt: str, task_type: str) -> str:
        r = self._script[self._i]
        self._i += 1
        return r if isinstance(r, str) else ""

    async def complete_with_tools(self, prompt: str, tools: list[dict]):
        self.offered_tools.append(list(tools))
        r = self._script[self._i]
        self._i += 1
        return r

    async def complete_with_images(self, prompt, images, task_type) -> str:
        return ""


def _tool_def(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}


async def _gate_allow(name, args):
    return Decision.SILENT


def _wire(backend, all_tools):
    """Build the {"router", "gate_fn", "execute_fn", "all_tools"} context and
    wire it in via the plugin's seam -- mirrors how main.py's
    set_subagent_context(lambda: {...}) would supply it fresh each call."""
    router = ModelRouter(backends={"fake/x": backend})

    async def execute(name, args):
        return ToolResult(content=f"{name} ran", is_error=False)

    delegate_mod.set_subagent_context(
        lambda: {
            "router": router,
            "gate_fn": _gate_allow,
            "execute_fn": execute,
            "all_tools": all_tools,
        }
    )
    return router


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_plugin_registers_and_tool_appears_in_orchestrator():
    orc = MCPOrchestrator()
    orc.register(create(), required_capabilities=delegate_mod.REQUIRED_CAPABILITIES)
    names = {t.name for t in orc.list_tools()}
    assert "delegate" in names


def test_required_capabilities_is_empty():
    """delegate performs no capability-bearing primitive itself -- it only
    awaits run_subagent, which re-gates every nested tool call through the
    caller's own gate (ADR-0020 decision 7). Precedent: plugins/memory.py
    (Issue #79) is "First plugin to declare REQUIRED_CAPABILITIES =
    frozenset()"."""
    assert delegate_mod.REQUIRED_CAPABILITIES == frozenset()


async def test_declared_capabilities_do_not_resolve_to_deny():
    """The point of the frozenset() fix: SHELL_EXEC defaults to DENY
    (cerebral/security/gate.py) and check_capabilities takes the worst
    decision across the declared set -- declaring the full vocabulary would
    make delegate DENY-by-default on any profile without an explicit
    shell_exec grant. An empty declaration short-circuits to SILENT."""
    orc = MCPOrchestrator()
    orc.register(create(), required_capabilities=delegate_mod.REQUIRED_CAPABILITIES)
    decision = await orc.check_capabilities(
        "delegate", delegate_mod.REQUIRED_CAPABILITIES, None,
    )
    assert decision == Decision.SILENT


# ---------------------------------------------------------------------------
# delegate() -> run_subagent pass-through
# ---------------------------------------------------------------------------


async def test_delegate_passes_scoped_tools_through_and_returns_compact_content():
    backend = FakeBackend([ToolCall(name="echo", args={}), "final compact answer"])
    all_tools = [_tool_def("echo"), _tool_def("other"), _tool_def("delegate")]
    _wire(backend, all_tools)

    plugin = DelegatePlugin()
    result = await plugin.call_tool(
        "delegate", {"task": "do echo", "tools": ["echo"]}
    )

    assert isinstance(result, ToolResult)
    assert not result.is_error
    # Compact: the parent gets ONLY the sub-run's final text, nothing about
    # the intermediate "echo" step.
    assert result.content == "final compact answer"
    # Scoped: the sub-planner backend was offered exactly the requested
    # subset on every step (one tool-call step, one finishing step).
    assert len(backend.offered_tools) == 2
    for offered in backend.offered_tools:
        assert [t["name"] for t in offered] == ["echo"]


async def test_delegate_requires_task():
    plugin = DelegatePlugin()
    result = await plugin.call_tool("delegate", {})
    assert result.is_error
    assert "task" in result.content


# ---------------------------------------------------------------------------
# Unwired seam fails closed
# ---------------------------------------------------------------------------


async def test_unwired_seam_fails_closed():
    plugin = DelegatePlugin()  # set_subagent_context never called
    result = await plugin.call_tool("delegate", {"task": "do something"})
    assert result.is_error
    assert "not wired" in result.content


async def test_context_factory_returning_none_also_fails_closed():
    delegate_mod.set_subagent_context(lambda: None)
    plugin = DelegatePlugin()
    result = await plugin.call_tool("delegate", {"task": "do something"})
    assert result.is_error
    assert "not wired" in result.content


# ---------------------------------------------------------------------------
# No nesting (ADR-0020 decision 9)
# ---------------------------------------------------------------------------


async def test_sub_agent_never_offered_delegate():
    """Even when the caller's full tool registry includes "delegate" itself,
    the sub-planner backend must never see it -- run_subagent strips it, and
    this plugin must not defeat that by routing around the strip."""
    backend = FakeBackend(["done"])
    all_tools = [_tool_def("echo"), _tool_def("other"), _tool_def("delegate")]
    _wire(backend, all_tools)

    plugin = DelegatePlugin()
    await plugin.call_tool("delegate", {"task": "do something"})  # tools=None -> shortlist path

    for offered in backend.offered_tools:
        assert "delegate" not in {t["name"] for t in offered}
