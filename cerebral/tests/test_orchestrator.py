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
