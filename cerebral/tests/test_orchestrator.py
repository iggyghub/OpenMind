"""
MCP orchestrator tests — Issue #7.

Unit tests use injected fake plugins (no subprocess, no real MCP servers).
"""
import sys
import textwrap
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cerebral.mcp.orchestrator import (
    MCPOrchestrator,
    PluginRegistrationError,
    REASON_INVALID_TYPE,
    REASON_MISSING,
    REASON_UNKNOWN_CAPABILITY,
    Tool,
    ToolResult,
)
from cerebral.security import (
    CAPABILITY_VOCABULARY,
    Capability,
    CallFlags,
    Decision,
)


# ---------------------------------------------------------------------------
# Helpers — fake plugins
# ---------------------------------------------------------------------------

def _make_plugin(name: str, tools: list[str]) -> MagicMock:
    """Return a fake Plugin with the given tool names."""
    plugin = MagicMock()
    plugin.name = name
    plugin.list_tools.return_value = [
        Tool(name=t, description=f"{t} description", plugin=name, schema={})
        for t in tools
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    return plugin


# ---------------------------------------------------------------------------
# Slice 1 — list_tools() returns tools from a registered plugin
# ---------------------------------------------------------------------------

def test_list_tools_returns_registered_plugin_tools():
    orc = MCPOrchestrator()
    clock = _make_plugin("clock", ["get_time", "set_alarm"])
    orc.register(clock)
    names = [t.name for t in orc.list_tools()]
    assert "get_time" in names
    assert "set_alarm" in names


def test_list_tools_empty_when_no_plugins():
    orc = MCPOrchestrator()
    assert orc.list_tools() == []


def test_list_tools_aggregates_across_plugins():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    orc.register(_make_plugin("browser", ["open_url"]))
    names = [t.name for t in orc.list_tools()]
    assert "get_time" in names
    assert "open_url" in names


def test_list_tools_no_duplicates_when_plugin_takes_over():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("gmail", ["gmail_send", "gmail_search"]))
    orc.register(_make_plugin("google_workspace", ["gmail_send", "gmail_search", "calendar_create_event"]))
    names = [t.name for t in orc.list_tools()]
    assert names.count("gmail_send") == 1
    assert names.count("gmail_search") == 1
    assert "calendar_create_event" in names
    assert len(names) == 3


def test_registration_tool_count_for_reflects_original_count_after_takeover():
    # Regression: _plugins_list_event was computing tool_count by counting
    # _tool_index entries owned by the plugin. After google_workspace takes
    # over, superseded plugins (gmail, calendar, etc.) showed tool_count=0
    # even though they had registered tools. registration_tool_count_for()
    # must return the count frozen at register() time, not the current index.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("gmail", ["gmail_send", "gmail_search"]))
    orc.register(_make_plugin("google_workspace", ["gmail_send", "gmail_search", "calendar_create_event"]))
    assert orc.registration_tool_count_for("gmail") == 2
    assert orc.registration_tool_count_for("google_workspace") == 3
    assert orc.registration_tool_count_for("unknown") == 0


# ---------------------------------------------------------------------------
# Slice 2 — call_tool() routes to the correct plugin
# ---------------------------------------------------------------------------

async def test_call_tool_routes_to_correct_plugin():
    orc = MCPOrchestrator()
    clock = _make_plugin("clock", ["get_time"])
    browser = _make_plugin("browser", ["open_url"])
    orc.register(clock)
    orc.register(browser)

    await orc.call_tool("get_time", {})

    clock.call_tool.assert_called_once_with("get_time", {})
    browser.call_tool.assert_not_called()


async def test_call_tool_passes_args_through():
    orc = MCPOrchestrator()
    clock = _make_plugin("clock", ["set_alarm"])
    orc.register(clock)

    await orc.call_tool("set_alarm", {"time": "07:00"})

    clock.call_tool.assert_called_once_with("set_alarm", {"time": "07:00"})


async def test_call_tool_returns_plugin_result():
    orc = MCPOrchestrator()
    plugin = _make_plugin("clock", ["get_time"])
    plugin.call_tool.return_value = ToolResult(content="12:00")
    orc.register(plugin)

    result = await orc.call_tool("get_time", {})

    assert result.content == "12:00"
    assert not result.is_error


# ---------------------------------------------------------------------------
# Slice 3 — unknown tool → structured ToolResult error, no crash
# ---------------------------------------------------------------------------

async def test_unknown_tool_returns_error_result():
    orc = MCPOrchestrator()
    result = await orc.call_tool("does_not_exist", {})
    assert result.is_error
    assert "does_not_exist" in result.content


async def test_unknown_tool_does_not_raise():
    orc = MCPOrchestrator()
    try:
        result = await orc.call_tool("ghost_tool", {"x": 1})
    except Exception as exc:
        pytest.fail(f"call_tool raised unexpectedly: {exc}")
    assert result.is_error


async def test_plugin_exception_returns_error_result():
    orc = MCPOrchestrator()
    broken = _make_plugin("broken", ["bad_tool"])
    broken.call_tool.side_effect = RuntimeError("plugin exploded")
    orc.register(broken)

    result = await orc.call_tool("bad_tool", {})

    assert result.is_error
    assert "bad_tool" in result.content


# ---------------------------------------------------------------------------
# Slice 4 — unregister() removes that plugin's tools
# ---------------------------------------------------------------------------

def test_unregister_removes_tools_from_list():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    orc.register(_make_plugin("browser", ["open_url"]))
    orc.unregister("clock")
    names = [t.name for t in orc.list_tools()]
    assert "get_time" not in names
    assert "open_url" in names


async def test_unregister_prevents_routing():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    orc.unregister("clock")
    result = await orc.call_tool("get_time", {})
    assert result.is_error


def test_unregister_unknown_plugin_does_not_raise():
    orc = MCPOrchestrator()
    try:
        orc.unregister("nonexistent")
    except Exception as exc:
        pytest.fail(f"unregister raised unexpectedly: {exc}")


def test_unregister_taker_restores_prior_owner():
    # Regression: when google_workspace took over gmail_send / calendar_*
    # from gmail / calendar / etc. and was then unregistered (the only
    # production path that triggers this is the builder plugin's uninstall
    # flow), the superseded tool used to silently disappear from
    # _tool_index — even though the original plugin was still registered
    # and still declared the tool. The orchestrator's view of the registry
    # diverged from the registered plugins' own list_tools(). Unregistering
    # the taker must restore the prior owner's claim.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("gmail", ["gmail_send", "gmail_search"]))
    orc.register(
        _make_plugin("google_workspace", ["gmail_send", "gmail_search"])
    )
    assert orc._tool_index["gmail_send"] == "google_workspace"

    orc.unregister("google_workspace")

    assert orc._tool_index["gmail_send"] == "gmail"
    assert orc._tool_index["gmail_search"] == "gmail"
    names = [t.name for t in orc.list_tools()]
    assert sorted(names) == ["gmail_search", "gmail_send"]


async def test_unregister_taker_restores_routing_to_prior_owner():
    # Companion to the previous test: not just the index, but dispatch
    # must route back to the restored owner — call_tool against a
    # superseded-then-restored tool should hit the original plugin.
    orc = MCPOrchestrator()
    gmail = _make_plugin("gmail", ["gmail_send"])
    workspace = _make_plugin("google_workspace", ["gmail_send"])
    orc.register(gmail)
    orc.register(workspace)

    await orc.call_tool("gmail_send", {"to": "x"})
    workspace.call_tool.assert_called_once()
    gmail.call_tool.assert_not_called()

    orc.unregister("google_workspace")

    await orc.call_tool("gmail_send", {"to": "y"})
    gmail.call_tool.assert_called_once_with("gmail_send", {"to": "y"})


def test_unregister_three_step_takeover_chain_restores_in_reverse():
    # Build an A -> B -> C takeover chain; unregistering in reverse must
    # restore B then A. Asserts the registration history is honoured as a
    # stack, not just two-level fallback.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("a", ["t1"]))
    orc.register(_make_plugin("b", ["t1"]))
    orc.register(_make_plugin("c", ["t1"]))
    assert orc._tool_index["t1"] == "c"

    orc.unregister("c")
    assert orc._tool_index["t1"] == "b"

    orc.unregister("b")
    assert orc._tool_index["t1"] == "a"


def test_unregister_prior_owner_keeps_taker_active():
    # Symmetric to the restoration case: when the original owner leaves
    # but the taker remains, the taker keeps the tool. This already worked
    # under the pre-fix _remove_from_index (which only walked entries
    # currently owned by the unregistering plugin) so the assertion here
    # is a guardrail against regressions from the history-walk rewrite.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("a", ["t1"]))
    orc.register(_make_plugin("b", ["t1"]))

    orc.unregister("a")

    assert orc._tool_index["t1"] == "b"
    names = [t.name for t in orc.list_tools()]
    assert names == ["t1"]


# ---------------------------------------------------------------------------
# Slice 5 — runtime registration makes tools immediately available
# ---------------------------------------------------------------------------

async def test_runtime_registration_tools_immediately_available():
    orc = MCPOrchestrator()
    # No plugins registered yet
    assert orc.list_tools() == []

    plugin = _make_plugin("notes", ["save_note"])
    orc.register(plugin)

    # Tools available without restart
    names = [t.name for t in orc.list_tools()]
    assert "save_note" in names

    result = await orc.call_tool("save_note", {"text": "hello"})
    assert not result.is_error


# ---------------------------------------------------------------------------
# Slice 6 — discover_plugins() auto-loads from directory
# ---------------------------------------------------------------------------

def test_discover_plugins_loads_valid_plugin(tmp_path):
    plugin_code = textwrap.dedent("""
        from cerebral.mcp.orchestrator import Tool, ToolResult

        PLUGIN_NAME = "test_clock"
        REQUIRED_CAPABILITIES = frozenset({"device_control"})

        class _ClockPlugin:
            name = "test_clock"
            def list_tools(self):
                return [Tool(name="tick", description="tick-tock", plugin="test_clock")]
            async def call_tool(self, tool_name, args):
                return ToolResult(content="tock")

        def create():
            return _ClockPlugin()
    """)
    (tmp_path / "test_clock.py").write_text(plugin_code)

    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    names = [t.name for t in orc.list_tools()]
    assert "tick" in names


def test_discover_plugins_skips_files_without_create(tmp_path):
    (tmp_path / "not_a_plugin.py").write_text("x = 1\n")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.list_tools() == []


def test_discover_plugins_skips_private_files(tmp_path):
    (tmp_path / "__helper.py").write_text("def create(): pass\n")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.list_tools() == []


def test_discover_plugins_nonexistent_dir_does_not_raise():
    orc = MCPOrchestrator()
    try:
        orc.discover_plugins(Path("/no/such/path"))
    except Exception as exc:
        pytest.fail(f"discover_plugins raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Issue #153 — get_plugin_module exposes the orchestrator-loaded instance
# ---------------------------------------------------------------------------
# Background: `cerebral/main.py` previously wired per-plugin injection seams
# (set_token_provider, set_memory_factory) via `import plugins.X` at module
# scope. The orchestrator loads the same file via
# importlib.util.spec_from_file_location under a DIFFERENT module name
# (openmind_plugin_<stem>), creating a second module object with its own
# module-level globals. The wiring landed on instance A; tool dispatch ran
# instance B; every set_*_provider call was silently a no-op for the
# production path. The #153 fix routes wiring through
# orc.get_plugin_module(name) so it targets the module the orchestrator
# actually dispatches against.

async def test_get_plugin_module_returns_dispatch_module(tmp_path):
    """The module returned by get_plugin_module must be the SAME object
    the dispatched tool reads from — proves seam wiring lands where tool
    dispatch can see it. Mutating a module global through get_plugin_module
    is observable at call_tool time."""
    plugin_code = textwrap.dedent("""
        from cerebral.mcp.orchestrator import Tool, ToolResult

        PLUGIN_NAME = "wired_probe"
        REQUIRED_CAPABILITIES = frozenset()

        _factory = None

        def set_factory(fn):
            global _factory
            _factory = fn

        class _Probe:
            name = "wired_probe"
            def list_tools(self):
                return [Tool(name="probe_read", description="reads factory", plugin="wired_probe")]
            async def call_tool(self, tool_name, args):
                if _factory is None:
                    return ToolResult(content="UNWIRED", is_error=True)
                return ToolResult(content=_factory())

        def create():
            return _Probe()
    """)
    (tmp_path / "wired_probe.py").write_text(plugin_code)

    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    module = orc.get_plugin_module("wired_probe")
    module.set_factory(lambda: "WIRED")

    result = await orc.call_tool("probe_read", {})
    assert result.content == "WIRED", (
        "Tool dispatch read the factory from the same module instance "
        "main.py would wire — proves #153 fix targets the dispatch path"
    )


def test_get_plugin_module_unknown_plugin_raises_keyerror():
    """A wiring attempt against a missing/refused plugin must surface
    loudly via KeyError — silently no-op wiring is exactly the #153
    failure mode this seam exists to prevent."""
    orc = MCPOrchestrator()
    with pytest.raises(KeyError, match="wired_probe"):
        orc.get_plugin_module("wired_probe")


def test_get_plugin_module_register_only_plugin_raises_keyerror():
    """Plugins added via the direct `register()` API (no on-disk file —
    tests, parked builder) have no source module to inject into; the seam
    must surface that as KeyError, not silently return None."""
    orc = MCPOrchestrator()
    plugin = _make_plugin("inline", ["t"])
    orc.register(plugin)
    with pytest.raises(KeyError, match="inline"):
        orc.get_plugin_module("inline")


def test_unregister_drops_module(tmp_path):
    """Bookkeeping: unregister tears down the module reference so a
    later get_plugin_module call doesn't return a stale ModuleType."""
    plugin_code = textwrap.dedent("""
        from cerebral.mcp.orchestrator import Tool, ToolResult

        PLUGIN_NAME = "ephemeral"
        REQUIRED_CAPABILITIES = frozenset()

        class _E:
            name = "ephemeral"
            def list_tools(self):
                return [Tool(name="e_tool", description="x", plugin="ephemeral")]
            async def call_tool(self, tool_name, args):
                return ToolResult(content="ok")

        def create():
            return _E()
    """)
    (tmp_path / "ephemeral.py").write_text(plugin_code)

    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.get_plugin_module("ephemeral") is not None

    orc.unregister("ephemeral")
    with pytest.raises(KeyError):
        orc.get_plugin_module("ephemeral")


# ---------------------------------------------------------------------------
# Slice 7 — tools_for_llm formats tool list for model router
# ---------------------------------------------------------------------------

def test_tools_for_llm_returns_list_of_dicts():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    result = orc.tools_for_llm
    assert isinstance(result, list)
    assert len(result) == 1


def test_tools_for_llm_has_required_fields():
    orc = MCPOrchestrator()
    plugin = _make_plugin("clock", ["get_time"])
    plugin.list_tools.return_value = [
        Tool(
            name="get_time",
            description="Returns current time",
            plugin="clock",
            schema={"type": "object", "properties": {"timezone": {"type": "string"}}},
        )
    ]
    orc.register(plugin)
    tool = orc.tools_for_llm[0]
    assert tool["name"] == "get_time"
    assert "description" in tool
    assert "input_schema" in tool


def test_tools_for_llm_empty_when_no_plugins():
    orc = MCPOrchestrator()
    assert orc.tools_for_llm == []


# ---------------------------------------------------------------------------
# Slice 8 — capability gate on the call path (Issue #43, ADR-0005)
# ---------------------------------------------------------------------------

async def test_call_tool_without_capability_proceeds_unchanged():
    # Existing call sites (pre-#44) pass no capability — behaviour preserved.
    orc = MCPOrchestrator()
    plugin = _make_plugin("notes", ["save_note"])
    orc.register(plugin)

    result = await orc.call_tool("save_note", {"text": "hi"})

    assert not result.is_error
    plugin.call_tool.assert_called_once_with("save_note", {"text": "hi"})


async def test_call_tool_silent_capability_dispatches():
    orc = MCPOrchestrator()
    plugin = _make_plugin("files", ["read_file"])
    orc.register(plugin)

    result = await orc.call_tool(
        "read_file", {"path": "x"}, capability=Capability.FS_READ
    )

    assert not result.is_error
    plugin.call_tool.assert_called_once_with("read_file", {"path": "x"})


async def test_call_tool_ask_capability_denies_fail_closed():
    # In this slice ASK resolves to DENY — the consent surface lands in #48.
    orc = MCPOrchestrator()
    plugin = _make_plugin("files", ["write_file"])
    orc.register(plugin)

    result = await orc.call_tool(
        "write_file", {"path": "x"}, capability=Capability.FS_WRITE
    )

    assert result.is_error
    assert "fs_write" in result.content
    plugin.call_tool.assert_not_called()


async def test_call_tool_deny_capability_blocks_dispatch():
    orc = MCPOrchestrator()
    plugin = _make_plugin("shell", ["run"])
    orc.register(plugin)

    result = await orc.call_tool("run", {"cmd": "ls"}, capability=Capability.SHELL_EXEC)

    assert result.is_error
    assert "shell_exec" in result.content
    plugin.call_tool.assert_not_called()


async def test_call_tool_passive_escalates_silent_to_deny():
    # passive=True on a silent class escalates to ASK; ASK→DENY in this slice.
    orc = MCPOrchestrator()
    plugin = _make_plugin("files", ["read_file"])
    orc.register(plugin)

    result = await orc.call_tool(
        "read_file", {"path": "x"},
        capability=Capability.FS_READ,
        flags=CallFlags(passive=True),
    )

    assert result.is_error
    plugin.call_tool.assert_not_called()


async def test_call_tool_passive_escalates_ask_to_deny():
    orc = MCPOrchestrator()
    plugin = _make_plugin("net", ["scan"])
    orc.register(plugin)

    result = await orc.call_tool(
        "scan", {},
        capability=Capability.NETWORK_RECON,
        flags=CallFlags(passive=True),
    )

    assert result.is_error
    plugin.call_tool.assert_not_called()


async def test_call_tool_gate_error_does_not_invoke_plugin():
    # Regression guard: if the gate blocks, the plugin never sees the args.
    orc = MCPOrchestrator()
    plugin = _make_plugin("shell", ["run"])
    plugin.call_tool.side_effect = AssertionError("plugin must not be called")
    orc.register(plugin)

    result = await orc.call_tool("run", {}, capability=Capability.SHELL_EXEC)

    assert result.is_error
    plugin.call_tool.assert_not_called()


async def test_call_tool_unknown_tool_short_circuits_before_gate():
    # Unknown tool returns its existing error without consulting the gate.
    orc = MCPOrchestrator()
    result = await orc.call_tool(
        "ghost", {}, capability=Capability.SHELL_EXEC
    )
    assert result.is_error
    assert "ghost" in result.content


# ---------------------------------------------------------------------------
# Slice 9 — REQUIRED_CAPABILITIES enforcement (Issue #44, ADR-0005)
# ---------------------------------------------------------------------------

def _plugin_module_source(plugin_name: str, capabilities_line: str = "") -> str:
    """Stand-alone plugin module source with a configurable capabilities decl."""
    return textwrap.dedent(f"""
        from cerebral.mcp.orchestrator import Tool, ToolResult

        PLUGIN_NAME = {plugin_name!r}
        {capabilities_line}

        class _P:
            name = {plugin_name!r}
            def list_tools(self):
                return [Tool(name="ping", description="ping", plugin={plugin_name!r})]
            async def call_tool(self, tool_name, args):
                return ToolResult(content="pong")

        def create():
            return _P()
    """)


def test_discover_refuses_plugin_missing_required_capabilities(tmp_path):
    (tmp_path / "broken.py").write_text(_plugin_module_source("broken"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    assert len(orc.registration_errors) == 1
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "broken"
    assert err["reason"] == REASON_MISSING
    assert "REQUIRED_CAPABILITIES" in err["detail"]


def test_discover_accepts_valid_required_capabilities(tmp_path):
    (tmp_path / "good.py").write_text(
        _plugin_module_source(
            "good",
            'REQUIRED_CAPABILITIES = frozenset({"fs_read"})',
        )
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert [t.name for t in orc.list_tools()] == ["ping"]
    assert orc.registration_errors == []
    assert orc.required_capabilities_for("good") == frozenset({"fs_read"})


def test_discover_refuses_unknown_capability_string(tmp_path):
    (tmp_path / "alien.py").write_text(
        _plugin_module_source(
            "alien",
            'REQUIRED_CAPABILITIES = frozenset({"fs_read", "telepathy"})',
        )
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "alien"
    assert err["reason"] == REASON_UNKNOWN_CAPABILITY
    assert "telepathy" in err["detail"]


def test_discover_refuses_invalid_required_capabilities_type(tmp_path):
    (tmp_path / "wrongtype.py").write_text(
        _plugin_module_source(
            "wrongtype",
            'REQUIRED_CAPABILITIES = ["fs_read"]',  # list, not frozenset
        )
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "wrongtype"
    assert err["reason"] == REASON_INVALID_TYPE


def test_discover_refuses_non_str_capability_value(tmp_path):
    (tmp_path / "intval.py").write_text(
        _plugin_module_source(
            "intval",
            "REQUIRED_CAPABILITIES = frozenset({1, 2, 3})",
        )
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    err = orc.registration_errors[0]
    assert err["reason"] == REASON_INVALID_TYPE
    assert "str" in err["detail"]


def test_discover_accepts_empty_frozenset(tmp_path):
    # A plugin with no capabilities is legitimate (e.g. a pure pass-through).
    (tmp_path / "inert.py").write_text(
        _plugin_module_source(
            "inert",
            "REQUIRED_CAPABILITIES = frozenset()",
        )
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.required_capabilities_for("inert") == frozenset()
    assert orc.registration_errors == []


def test_discover_create_failure_recorded(tmp_path):
    (tmp_path / "explodes.py").write_text(textwrap.dedent("""
        PLUGIN_NAME = "explodes"
        REQUIRED_CAPABILITIES = frozenset({"fs_read"})
        def create():
            raise RuntimeError("boom")
    """))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    err = orc.registration_errors[0]
    assert err["plugin_name"] == "explodes"
    assert err["reason"] == "create_failed"
    assert "boom" in err["detail"]


def test_discover_does_not_call_create_when_declaration_missing(tmp_path):
    # create() must not run when the constant is missing — otherwise a
    # malformed plugin's side effects (DB writes, network calls) leak.
    sentinel = tmp_path / "create_called.txt"
    (tmp_path / "sideeffect.py").write_text(textwrap.dedent(f"""
        from pathlib import Path
        PLUGIN_NAME = "sideeffect"
        def create():
            Path({str(sentinel)!r}).write_text("called")
    """))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert not sentinel.exists()
    assert orc.registration_errors[0]["reason"] == REASON_MISSING


def test_discover_partial_refusal_leaves_other_plugins_intact(tmp_path):
    (tmp_path / "good.py").write_text(
        _plugin_module_source(
            "good",
            'REQUIRED_CAPABILITIES = frozenset({"clipboard"})',
        )
    )
    (tmp_path / "broken.py").write_text(_plugin_module_source("broken"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert [t.name for t in orc.list_tools()] == ["ping"]
    assert "broken" in {e["plugin_name"] for e in orc.registration_errors}
    assert "good" not in {e["plugin_name"] for e in orc.registration_errors}


def test_register_with_invalid_required_capabilities_raises():
    orc = MCPOrchestrator()
    plugin = _make_plugin("phantom", ["t"])
    with pytest.raises(PluginRegistrationError) as exc:
        orc.register(plugin, required_capabilities=frozenset({"not_a_class"}))
    assert exc.value.reason == REASON_UNKNOWN_CAPABILITY
    # Plugin must not have been added to the registry.
    assert orc.list_tools() == []


def test_register_with_valid_required_capabilities_stores_them():
    orc = MCPOrchestrator()
    plugin = _make_plugin("notes", ["save_note"])
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))
    assert orc.required_capabilities_for("notes") == frozenset({"fs_write"})


def test_register_without_required_capabilities_backward_compatible():
    # Tests / direct callers can omit the kwarg and behavior matches pre-#44.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    assert [t.name for t in orc.list_tools()] == ["get_time"]
    assert orc.required_capabilities_for("clock") is None


def test_unregister_clears_required_capabilities():
    orc = MCPOrchestrator()
    plugin = _make_plugin("notes", ["save_note"])
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))
    orc.unregister("notes")
    assert orc.required_capabilities_for("notes") is None


def test_registration_errors_is_a_copy_not_internal_list():
    # External callers must not be able to mutate the orchestrator's record.
    orc = MCPOrchestrator()
    snapshot = orc.registration_errors
    snapshot.append({"plugin_name": "fake"})
    assert orc.registration_errors == []


# ---------------------------------------------------------------------------
# Slice 10 — every real plugin module declares a valid REQUIRED_CAPABILITIES
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGINS_DIR = _REPO_ROOT / "plugins"
_PLUGIN_FILES = sorted(
    p for p in _PLUGINS_DIR.glob("*.py") if not p.name.startswith("_")
)


# ---------------------------------------------------------------------------
# Slice 11 — ACL resolver integration on the call path (Issue #45)
# ---------------------------------------------------------------------------


def _build_acl(tmp_path):
    """Construct a ProfileACL with its own ephemeral profile."""
    from cerebral.db.profiles import ProfileManager
    from cerebral.security import ProfileACL
    pm = ProfileManager(db_path=tmp_path / "openmind.db")
    profile = pm.create(name="Test")
    return ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    ), pm, profile


async def test_orchestrator_consults_acl_when_set(tmp_path):
    """When an ACL is wired in, call_tool resolves through it (not the
    bare gate). Per-tool overrides at SILENT let ASK-class calls through."""
    acl, _, _ = _build_acl(tmp_path)
    acl.set_tool_override("write_file", Decision.SILENT)
    orc = MCPOrchestrator(acl=acl)
    plugin = _make_plugin("files", ["write_file"])
    orc.register(plugin)
    result = await orc.call_tool(
        "write_file", {"path": "x"}, capability=Capability.FS_WRITE,
    )
    assert not result.is_error
    plugin.call_tool.assert_called_once()


async def test_orchestrator_acl_blocks_persistent_deny(tmp_path):
    """A persistent class-level DENY blocks even when the default would
    have been SILENT."""
    acl, _, _ = _build_acl(tmp_path)
    acl.set_persistent_class(Capability.FS_READ, Decision.DENY)
    orc = MCPOrchestrator(acl=acl)
    plugin = _make_plugin("files", ["read_file"])
    orc.register(plugin)
    result = await orc.call_tool(
        "read_file", {"path": "x"}, capability=Capability.FS_READ,
    )
    assert result.is_error
    plugin.call_tool.assert_not_called()


async def test_orchestrator_acl_consumes_once_grant(tmp_path):
    """A once-grant lets one call through, then the next call falls back."""
    acl, _, _ = _build_acl(tmp_path)
    acl.grant_once(Capability.FS_WRITE, Decision.SILENT)
    orc = MCPOrchestrator(acl=acl)
    plugin = _make_plugin("files", ["write_file"])
    orc.register(plugin)
    # First call: silent, dispatches.
    r1 = await orc.call_tool(
        "write_file", {"path": "x"}, capability=Capability.FS_WRITE,
    )
    assert not r1.is_error
    # Second call: once-grant consumed, default ASK → DENY at orchestrator.
    r2 = await orc.call_tool(
        "write_file", {"path": "x"}, capability=Capability.FS_WRITE,
    )
    assert r2.is_error
    assert plugin.call_tool.call_count == 1


async def test_orchestrator_acl_passive_escalation_defeats_persistent_silent(tmp_path):
    """Regression: even a persistent SILENT grant for the class doesn't let
    a queue-originated (passive=True) call through silently."""
    acl, _, _ = _build_acl(tmp_path)
    acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    orc = MCPOrchestrator(acl=acl)
    plugin = _make_plugin("files", ["write_file"])
    orc.register(plugin)
    result = await orc.call_tool(
        "write_file", {"path": "x"},
        capability=Capability.FS_WRITE,
        flags=CallFlags(passive=True),
    )
    assert result.is_error
    plugin.call_tool.assert_not_called()


def test_orchestrator_set_acl_replaces_resolver(tmp_path):
    """set_acl swaps the resolver — used on profile switch."""
    acl1, _, _ = _build_acl(tmp_path)
    orc = MCPOrchestrator(acl=acl1)
    assert orc.acl is acl1
    acl2, _, _ = _build_acl(tmp_path / "alt")
    orc.set_acl(acl2)
    assert orc.acl is acl2
    orc.set_acl(None)
    assert orc.acl is None


async def test_orchestrator_without_acl_falls_back_to_gate(tmp_path):
    """Backward compat: no ACL → call path uses the bare gate (pre-#45)."""
    orc = MCPOrchestrator()
    assert orc.acl is None
    plugin = _make_plugin("files", ["read_file"])
    orc.register(plugin)
    result = await orc.call_tool(
        "read_file", {"path": "x"}, capability=Capability.FS_READ,
    )
    assert not result.is_error


# ---------------------------------------------------------------------------
# Slice 12 — plugin_for_tool lookup (Issue #51, ADR-0005)
# ---------------------------------------------------------------------------


def test_plugin_for_tool_returns_owning_plugin_name():
    """The orchestrator owns the tool→plugin mapping; the ACL's new-plugin
    flag hook depends on it to translate a tool name back to its plugin."""
    orc = MCPOrchestrator()
    plugin = _make_plugin("weatherbug", ["weatherbug_ping", "weatherbug_report"])
    orc.register(plugin)
    assert orc.plugin_for_tool("weatherbug_ping") == "weatherbug"
    assert orc.plugin_for_tool("weatherbug_report") == "weatherbug"


def test_plugin_for_tool_returns_none_for_unknown_tool():
    orc = MCPOrchestrator()
    assert orc.plugin_for_tool("never_existed") is None


def test_plugin_for_tool_drops_mapping_on_unregister():
    orc = MCPOrchestrator()
    plugin = _make_plugin("weatherbug", ["weatherbug_ping"])
    orc.register(plugin)
    orc.unregister("weatherbug")
    assert orc.plugin_for_tool("weatherbug_ping") is None


@pytest.mark.parametrize("plugin_path", _PLUGIN_FILES, ids=lambda p: p.stem)
def test_every_real_plugin_declares_valid_required_capabilities(plugin_path):
    """Loading every plugin file via discover_plugins must not refuse it.

    This is the migration's correctness guarantee: every module under
    plugins/ declares REQUIRED_CAPABILITIES as a frozenset[str] whose values
    are all in the 16-class vocabulary.
    """
    # Discover only this one plugin to isolate failures.
    src_root = str(_REPO_ROOT)
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    orc = MCPOrchestrator()
    # We can't call discover_plugins on a single file; replicate the relevant
    # checks here by reading the module attribute directly.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"openmind_audit_{plugin_path.stem}", plugin_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required = getattr(module, "REQUIRED_CAPABILITIES", None)
    assert required is not None, (
        f"{plugin_path.name} is missing REQUIRED_CAPABILITIES (Issue #44)"
    )
    assert isinstance(required, frozenset), (
        f"{plugin_path.name}.REQUIRED_CAPABILITIES must be frozenset"
    )
    assert all(isinstance(c, str) for c in required), (
        f"{plugin_path.name}.REQUIRED_CAPABILITIES must contain only str"
    )
    unknown = required - CAPABILITY_VOCABULARY
    assert not unknown, (
        f"{plugin_path.name} declares unknown capabilities: {sorted(unknown)}"
    )


# ---------------------------------------------------------------------------
# Slice 10 — check_capabilities() (Issue #52)
#
# Looping `call_tool` per declared capability would dispatch the side-
# effectful tool the moment the first SILENT cap resolves, before the
# remaining caps are checked. `check_capabilities` runs the ACL / gate /
# modal / consent stack across the full set and returns the worst Decision
# WITHOUT invoking the tool, so the caller can AND the caps cleanly and
# then dispatch exactly once.
# ---------------------------------------------------------------------------


class _RecordingConsent:
    """Duck-typed ConsentSurface. Returns the queued decision, records calls."""

    def __init__(self, *decisions) -> None:
        self.decisions = list(decisions) or [Decision.SILENT]
        self.received: list[dict] = []

    async def request(self, capability, tool_name, args, flags=None):
        self.received.append({"capability": capability, "tool_name": tool_name})
        return self.decisions.pop(0) if self.decisions else Decision.SILENT

    def set_acl(self, acl) -> None:
        pass


class _RecordingModal:
    """Duck-typed ModalSurface. Returns the queued decision, records calls."""

    def __init__(self, *decisions) -> None:
        self.decisions = list(decisions) or [Decision.SILENT]
        self.received: list[dict] = []

    async def request(self, capability, tool_name, args, flags=None):
        self.received.append({"capability": capability, "tool_name": tool_name})
        return self.decisions.pop(0) if self.decisions else Decision.SILENT


async def test_check_capabilities_empty_set_returns_silent():
    # An unconstrained call (no declared capabilities) is SILENT.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("notes", ["save_note"]))

    decision = await orc.check_capabilities("save_note", frozenset(), None)

    assert decision is Decision.SILENT


async def test_check_capabilities_unknown_tool_denies():
    # Defensive — never invoke and never silently allow a tool that no
    # plugin owns. (approve_item routes through here; a stale queue
    # item must not slip through.)
    orc = MCPOrchestrator()

    decision = await orc.check_capabilities("ghost_tool", frozenset({"fs_read"}), None)

    assert decision is Decision.DENY


async def test_check_capabilities_silent_cap_returns_silent():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("files", ["read_file"]))

    decision = await orc.check_capabilities(
        "read_file", frozenset({"fs_read"}), None,
    )

    assert decision is Decision.SILENT


async def test_check_capabilities_deny_cap_returns_deny():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("shell", ["run"]))

    decision = await orc.check_capabilities(
        "run", frozenset({"shell_exec"}), None,
    )

    assert decision is Decision.DENY


async def test_check_capabilities_takes_worst_across_set():
    # AND semantics: a SILENT + an ASK cap → ASK (worst wins).
    orc = MCPOrchestrator()
    orc.register(_make_plugin("mix", ["mixed_tool"]))

    decision = await orc.check_capabilities(
        "mixed_tool", frozenset({"fs_read", "fs_write"}), None,
    )

    # fs_read is SILENT, fs_write is ASK → worst is ASK (fail-closed
    # to DENY when no consent surface wired).
    assert decision is Decision.DENY


async def test_check_capabilities_deny_beats_ask_and_silent():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("mix", ["t"]))

    decision = await orc.check_capabilities(
        "t",
        frozenset({"fs_read", "fs_write", "shell_exec"}),
        None,
    )

    assert decision is Decision.DENY


async def test_check_capabilities_does_not_invoke_plugin():
    # The whole point — a tool with a SILENT cap must NOT execute when
    # we're only checking. (Loop-call_tool would have dispatched it.)
    orc = MCPOrchestrator()
    plugin = _make_plugin("files", ["read_file"])
    plugin.call_tool.side_effect = AssertionError("plugin must not run during check")
    orc.register(plugin)

    decision = await orc.check_capabilities(
        "read_file", frozenset({"fs_read"}), None,
    )

    assert decision is Decision.SILENT
    plugin.call_tool.assert_not_called()


async def test_check_capabilities_does_not_invoke_plugin_for_silent_subset():
    # AND-semantics regression: even when the FIRST cap iterated is SILENT,
    # the worst-across-set wins and the plugin still doesn't run.
    orc = MCPOrchestrator()
    plugin = _make_plugin("mix", ["t"])
    plugin.call_tool.side_effect = AssertionError("plugin must not run during check")
    orc.register(plugin)

    decision = await orc.check_capabilities(
        "t", frozenset({"fs_read", "shell_exec"}), None,
    )

    assert decision is Decision.DENY
    plugin.call_tool.assert_not_called()


async def test_check_capabilities_passive_escalates_per_cap():
    # passive=True flag must propagate into each per-cap resolve().
    # fs_read (SILENT) with passive → ASK; consent surface answers DENY.
    orc = MCPOrchestrator()
    orc.register(_make_plugin("files", ["read_file"]))

    decision = await orc.check_capabilities(
        "read_file",
        frozenset({"fs_read"}),
        CallFlags(passive=True),
    )

    # No consent surface → ASK fails closed.
    assert decision is Decision.DENY


async def test_check_capabilities_consent_routed_once_for_ask():
    # The consent surface must be called at most once per check_capabilities
    # invocation — we don't pester the user with one prompt per cap.
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(consent=consent)
    orc.register(_make_plugin("files", ["write_file"]))

    decision = await orc.check_capabilities(
        "write_file", frozenset({"fs_write"}), None,
    )

    assert decision is Decision.SILENT
    assert len(consent.received) == 1


async def test_check_capabilities_consent_called_once_even_with_multiple_ask_caps():
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(consent=consent)
    orc.register(_make_plugin("mix", ["t"]))

    decision = await orc.check_capabilities(
        "t", frozenset({"fs_write", "external_data_write"}), None,
    )

    assert decision is Decision.SILENT
    # Worst is ASK; consent prompts ONCE with the worst cap.
    assert len(consent.received) == 1


async def test_check_capabilities_irreversible_routes_to_modal():
    modal = _RecordingModal(Decision.SILENT)
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(consent=consent, modal=modal)
    orc.register(_make_plugin("files", ["delete_file"]))

    decision = await orc.check_capabilities(
        "delete_file",
        frozenset({"fs_delete"}),
        CallFlags(irreversible=True),
    )

    assert decision is Decision.SILENT
    assert len(modal.received) == 1
    # Modal supersedes consent — surface must NOT also prompt.
    assert len(consent.received) == 0


async def test_check_capabilities_irreversible_skipped_when_already_deny():
    # If the worst cap is DENY, irreversible doesn't even reach the modal.
    modal = _RecordingModal()
    orc = MCPOrchestrator(modal=modal)
    orc.register(_make_plugin("shell", ["run"]))

    decision = await orc.check_capabilities(
        "run",
        frozenset({"shell_exec"}),
        CallFlags(irreversible=True),
    )

    assert decision is Decision.DENY
    assert len(modal.received) == 0


async def test_check_capabilities_consent_denial_propagates():
    consent = _RecordingConsent(Decision.DENY)
    orc = MCPOrchestrator(consent=consent)
    orc.register(_make_plugin("files", ["write_file"]))

    decision = await orc.check_capabilities(
        "write_file", frozenset({"fs_write"}), None,
    )

    assert decision is Decision.DENY


# ---------------------------------------------------------------------------
# Slice 12 — per-tool irreversible declaration (Issue #139, ADR-0005)
#
# Tools opt in to the modal-routing branch by setting Tool(irreversible=True).
# The orchestrator OR-merges the declaration into CallFlags at the start of
# call_tool and check_capabilities, so the modal fires even when the caller
# passes flags=None. Caller-supplied irreversible=True is never lost (it OR's
# into the declaration). The 16-class vocabulary and modal mechanism are
# unchanged — this slice only wires per-tool metadata to the flag.
# ---------------------------------------------------------------------------


def _make_plugin_with_tool(plugin_name: str, tool: Tool) -> MagicMock:
    """Build a fake plugin whose list_tools returns the given Tool object
    verbatim — needed when the test sets tool-shape fields like irreversible
    that the simpler ``_make_plugin`` helper hard-codes to defaults."""
    plugin = MagicMock()
    plugin.name = plugin_name
    plugin.list_tools.return_value = [tool]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    return plugin


def test_tool_defaults_irreversible_false():
    # Pre-#139 callers construct Tool with four positional/keyword args.
    # The new field defaults to False so every existing call site keeps
    # working unmodified.
    t = Tool(name="t", description="d", plugin="p")
    assert t.irreversible is False


def test_tool_accepts_irreversible_true():
    t = Tool(name="t", description="d", plugin="p", irreversible=True)
    assert t.irreversible is True


def test_tool_lookup_populated_on_register():
    # The dispatch path reads ``_tool_lookup[name].irreversible`` so the
    # register loop must populate it alongside ``_tool_index``.
    orc = MCPOrchestrator()
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(_make_plugin_with_tool("mail", tool))
    assert orc._tool_lookup["send"] is tool
    assert orc._tool_lookup["send"].irreversible is True


def test_tool_lookup_cleared_on_unregister():
    orc = MCPOrchestrator()
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(_make_plugin_with_tool("mail", tool))
    orc.unregister("mail")
    assert "send" not in orc._tool_lookup


def test_merge_irreversible_unchanged_when_declaration_false():
    # No allocation in the common case: declaration is False so the caller's
    # flags pass through unchanged (identity).
    orc = MCPOrchestrator()
    orc.register(_make_plugin("notes", ["save_note"]))
    flags_in = CallFlags(passive=True)
    flags_out = orc._merge_irreversible(flags_in, "save_note")
    assert flags_out is flags_in


def test_merge_irreversible_none_flags_when_declared():
    # flags=None + declaration=True → fresh CallFlags(irreversible=True).
    orc = MCPOrchestrator()
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(_make_plugin_with_tool("mail", tool))
    flags_out = orc._merge_irreversible(None, "send")
    assert flags_out is not None
    assert flags_out.irreversible is True
    assert flags_out.passive is False


def test_merge_irreversible_preserves_passive_when_declared():
    # Caller passes passive=True; declaration is True → both set on the
    # returned flags. Neither field is dropped.
    orc = MCPOrchestrator()
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(_make_plugin_with_tool("mail", tool))
    flags_out = orc._merge_irreversible(CallFlags(passive=True), "send")
    assert flags_out.irreversible is True
    assert flags_out.passive is True


def test_merge_irreversible_caller_true_passes_through():
    # Caller already opted in; declaration is False → flags returned
    # unchanged. The merge is one-way (caller can opt in even if the
    # declaration is False).
    orc = MCPOrchestrator()
    orc.register(_make_plugin("notes", ["save_note"]))
    flags_in = CallFlags(irreversible=True)
    flags_out = orc._merge_irreversible(flags_in, "save_note")
    assert flags_out is flags_in


def test_merge_irreversible_unknown_tool_passes_flags_unchanged():
    # Defensive — a tool name that isn't in _tool_lookup must not raise;
    # the dispatch path's later "unknown tool" branch handles refusal.
    orc = MCPOrchestrator()
    flags_in = CallFlags(passive=True)
    assert orc._merge_irreversible(flags_in, "ghost") is flags_in
    assert orc._merge_irreversible(None, "ghost") is None


async def test_call_tool_declared_irreversible_routes_to_modal():
    # End-to-end through call_tool: the caller passes flags=None and a
    # capability; the modal fires because the Tool declares irreversible.
    modal = _RecordingModal(Decision.SILENT)
    orc = MCPOrchestrator(modal=modal)
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(
        _make_plugin_with_tool("mail", tool),
        required_capabilities=frozenset({"external_data_write"}),
    )

    result = await orc.call_tool(
        "send", {"to": "x"}, capability=Capability.EXTERNAL_DATA_WRITE,
    )

    assert not result.is_error
    assert len(modal.received) == 1


async def test_call_tool_no_modal_wired_declared_irreversible_fails_closed():
    # With the declaration set but no modal surface attached, the modal
    # routing branch in call_tool fails closed to DENY — the same
    # invariant as for caller-supplied flags.irreversible=True.
    orc = MCPOrchestrator()
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    plugin = _make_plugin_with_tool("mail", tool)
    orc.register(plugin, required_capabilities=frozenset({"external_data_write"}))

    result = await orc.call_tool(
        "send", {"to": "x"}, capability=Capability.EXTERNAL_DATA_WRITE,
    )

    assert result.is_error
    plugin.call_tool.assert_not_called()


async def test_call_tool_undeclared_tool_does_not_route_to_modal():
    # Sanity: a Tool that does NOT declare irreversible AND a caller that
    # passes flags=None must NOT trigger the modal even if a surface is
    # wired. ASK → DENY via the normal consent-fail-closed path.
    modal = _RecordingModal(Decision.SILENT)
    orc = MCPOrchestrator(modal=modal)
    plugin = _make_plugin("files", ["write_file"])
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    result = await orc.call_tool(
        "write_file", {"path": "x"}, capability=Capability.FS_WRITE,
    )

    assert result.is_error  # ASK with no consent surface → DENY
    assert len(modal.received) == 0


async def test_check_capabilities_declared_irreversible_routes_to_modal():
    # Queue-path symmetry: check_capabilities ORs the declaration in
    # before the irreversible-routing branch fires. The handoff's most
    # production-relevant path because main.py:1167-1168 calls this with
    # CallFlags(passive=True) and benefits from the merge.
    modal = _RecordingModal(Decision.SILENT)
    consent = _RecordingConsent(Decision.SILENT)
    orc = MCPOrchestrator(consent=consent, modal=modal)
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(
        _make_plugin_with_tool("mail", tool),
        required_capabilities=frozenset({"external_data_write"}),
    )

    decision = await orc.check_capabilities(
        "send", frozenset({"external_data_write"}), None,
    )

    assert decision is Decision.SILENT
    assert len(modal.received) == 1
    assert len(consent.received) == 0


async def test_check_capabilities_declared_irreversible_passive_merges():
    # Queue calls run with passive=True today; the merge must produce
    # CallFlags(passive=True, irreversible=True). The ACL escalation
    # (passive→ASK) and the irreversible-modal routing both fire.
    modal = _RecordingModal(Decision.SILENT)
    orc = MCPOrchestrator(modal=modal)
    tool = Tool(name="send", description="d", plugin="mail", irreversible=True)
    orc.register(
        _make_plugin_with_tool("mail", tool),
        required_capabilities=frozenset({"external_data_write"}),
    )

    decision = await orc.check_capabilities(
        "send", frozenset({"external_data_write"}),
        CallFlags(passive=True),
    )

    # external_data_write is ASK; passive escalates ASK → DENY. The
    # irreversible-routing branch only fires when the gate did NOT
    # already DENY (sharpener #2 in #50), so the modal does NOT fire here.
    assert decision is Decision.DENY
    assert len(modal.received) == 0


# ---------------------------------------------------------------------------
# Slice 13 — repo-wide irreversible-tool allowlist (Issue #139)
#
# Catches accidental future drift: if a contributor marks a new tool
# without thinking through the modal-prompt UX cost, this test fails
# until the marking gets its own deliberate slice or amendment.
#
# Current allowlist:
#   - plugins/gmail.py          (gmail_send -- Issue #139 original slice)
#   - plugins/openclaw_channels.py (openclaw_permissions_respond -- Issue
#     #168; allow-once / allow-always to a gateway exec or plugin approval
#     cannot be undone, so the modal is the only consent surface that
#     matches the semantics)
#   - plugins/discord_user.py   (discord_send_message -- Issue #175 / ADR-0006;
#     a DM sent under the user's PERSONAL Discord account is irreversible:
#     once a real human reads it, no API edit/delete restores the prior
#     state of the conversation. The self-bot posture also makes mistaken
#     sends a detection signal, raising the ban probability. Modal-gating
#     is correct on top of the per-call ``confirm`` arg.)
#                                (discord_react / discord_edit / discord_delete
#     -- Issue #178 slice 3: reactions, edits, and deletes are also
#     detection vectors and mutate external state on a real Discord account.
#     Same ``irreversible=True`` + ``confirm``-gate posture as send.)
# ---------------------------------------------------------------------------

_IRREVERSIBLE_PLUGINS: frozenset[str] = frozenset({
    "gmail.py",
    "openclaw_channels.py",
    "discord_user.py",
    "google_docs.py",    # docs_create + docs_append (#224)
    "google_sheets.py",  # sheets_write_range + sheets_append_row + sheets_create (#225)
    "job_search.py",     # jobs_apply_submit (ADR-0009; sole exception to ADR-0005 irreversible-modal rule)
})


@pytest.mark.parametrize("plugin_path", _PLUGIN_FILES, ids=lambda p: p.stem)
def test_irreversible_marking_is_allowlisted(plugin_path):
    """Only plugins on the deliberate allowlist may declare irreversible=True.

    Future per-tool marking is a one-line edit per plugin AND a
    deliberate addition to ``_IRREVERSIBLE_PLUGINS`` above."""
    source = plugin_path.read_text(encoding="utf-8")
    has_decl = "irreversible=True" in source
    if plugin_path.name in _IRREVERSIBLE_PLUGINS:
        assert has_decl, (
            f"plugins/{plugin_path.name} is on the irreversible allowlist "
            "but no longer declares irreversible=True -- update the "
            "allowlist or restore the marking"
        )
    else:
        assert not has_decl, (
            f"plugins/{plugin_path.name} declares irreversible=True but is "
            "not on the irreversible allowlist. Add it deliberately to "
            "_IRREVERSIBLE_PLUGINS above when marking another tool."
        )
