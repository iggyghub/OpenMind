"""
Plugin inspectability tests — Issue #46, ADR-0005.

Covers:
  - `scan_source` returns an issue for every forbidden pattern and None for
    clean source.
  - `classify_path` recognises the three conforming layouts and refuses
    everything else with REASON_NOT_INSPECTABLE_PATH.
  - `MCPOrchestrator.discover_plugins` runs the scan on `plugins/<name>.py`
    and `plugins/<name>/server.py`, skips the scan on
    `plugins/_trusted/<name>/server.py` but still enforces
    REQUIRED_CAPABILITIES + gates at call time, and records non-conforming
    subdirs as REASON_NOT_INSPECTABLE_PATH.
  - The static-pattern scan fires BEFORE module import so a refused
    plugin's import-time side effects never run.
  - `MCPOrchestrator.inspectability_for` returns "inspected" / "trusted" /
    None as appropriate.
  - All 32 shipped plugin modules pass the canonical scan (real-plugin audit).
  - The builder consumes the canonical scan list — no duplicate
    `_FORBIDDEN_PATTERNS` definition lives in `plugins/builder.py`.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.mcp.orchestrator import (
    MCPOrchestrator,
    REASON_MISSING,
)
from cerebral.security import (
    Capability,
    INSPECTED,
    TRUSTED,
    REASON_FORBIDDEN_PATTERN,
    REASON_NON_TEXT,
    REASON_NOT_INSPECTABLE_PATH,
    classify_plugin_path,
    scan_source,
)
from cerebral.security.inspectability import FORBIDDEN_PATTERNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, source: str) -> Path:
    """Always-utf-8 write — the orchestrator reads with encoding='utf-8',
    so writes that fall through to the OS default codepage (cp1252 on
    en-US Windows) round-trip as REASON_NON_TEXT and mask the real test
    intent."""
    path.write_text(source, encoding="utf-8")
    return path


_OK_PLUGIN_TEMPLATE = textwrap.dedent("""
    from cerebral.mcp.orchestrator import Tool, ToolResult

    PLUGIN_NAME = {name!r}
    REQUIRED_CAPABILITIES = frozenset({{{capabilities!r}}})

    class _P:
        name = {name!r}
        def list_tools(self):
            return [Tool(name={tool!r}, description="t", plugin={name!r})]
        async def call_tool(self, tool_name, args):
            return ToolResult(content="ok")

    def create():
        return _P()
""")


def _ok_plugin_source(
    name: str = "clean", capability: str = "clipboard", tool: str = "ping",
) -> str:
    return _OK_PLUGIN_TEMPLATE.format(name=name, capabilities=capability, tool=tool)


def _bad_plugin_source(
    name: str, bad_line: str, capability: str = "clipboard",
) -> str:
    """A plugin whose body contains a forbidden pattern at module scope.

    The forbidden line lives at the top of the module so it would execute
    during import — verifying side-effect protection requires the scan to
    fire first.
    """
    return textwrap.dedent(f"""
        from cerebral.mcp.orchestrator import Tool, ToolResult
        {bad_line}

        PLUGIN_NAME = {name!r}
        REQUIRED_CAPABILITIES = frozenset({{{capability!r}}})

        class _P:
            name = {name!r}
            def list_tools(self):
                return [Tool(name="t", description="t", plugin={name!r})]
            async def call_tool(self, tool_name, args):
                return ToolResult(content="ok")

        def create():
            return _P()
    """)


# ---------------------------------------------------------------------------
# Slice 1 — scan_source: clean source + every forbidden pattern
# ---------------------------------------------------------------------------


def test_scan_source_returns_none_for_clean_source():
    assert scan_source(_ok_plugin_source()) is None


def test_scan_source_returns_none_for_empty_string():
    assert scan_source("") is None


@pytest.mark.parametrize(
    "bad_snippet, label_substring",
    [
        ("import os\nos.system('echo hi')", "os.system"),
        ("import subprocess\nsubprocess.run(['ls'])", "subprocess"),
        ("import subprocess\nsubprocess.Popen(['ls'])", "subprocess"),
        ("import subprocess\nsubprocess.call(['ls'])", "subprocess"),
        ("import subprocess\nsubprocess.check_output(['ls'])", "subprocess"),
        ("import subprocess\nsubprocess.check_call(['ls'])", "subprocess"),
        ("import os\nos.popen('ls')", "os.popen"),
        ("from os import system", "from os import system"),
        ("__import__('os')", "__import__('os')"),
        ("from subprocess import run", "from subprocess import"),
        ("__import__('subprocess')", "__import__('subprocess')"),
        ("exec('print(1)')", "exec"),
        ("eval('1+1')", "eval"),
        ("compile('1', '<s>', 'exec')", "compile"),
        ("import pickle\npickle.loads(b'')", "pickle.loads"),
        ("import marshal\nmarshal.loads(b'')", "marshal.loads"),
        ("open('x', 'w').write('y')", "raw file write"),
    ],
)
def test_scan_source_detects_forbidden_pattern(bad_snippet, label_substring):
    issue = scan_source(bad_snippet)
    assert issue is not None
    assert issue.reason == REASON_FORBIDDEN_PATTERN
    assert label_substring in issue.detail


def test_forbidden_patterns_list_covers_strict_superset_of_pre46_builder():
    """Regression: the pre-#46 builder shipped 8 patterns. The canonical
    list extends it; it MUST still include each original entry verbatim."""
    labels = {label for _, label in FORBIDDEN_PATTERNS}
    for required in {
        "os.system",
        "subprocess shell-out",
        "os.popen",
        "from os import system",
        "__import__('os')",
        "exec()",
        "eval()",
        "raw file write",
    }:
        assert required in labels, f"canonical list dropped {required!r}"


def test_scan_source_attribute_access_does_not_false_positive():
    """`thing.exec(...)` is not bare `exec(...)` — the original regex used
    a negative lookbehind to preserve that, and #46's strict superset must
    not regress it."""
    assert scan_source("plugin.exec_command('x')") is None
    assert scan_source("obj.eval_now()") is None


# ---------------------------------------------------------------------------
# Slice 2 — classify_path: three conforming layouts + non-conforming refusal
# ---------------------------------------------------------------------------


def test_classify_path_flat_form_is_inspected(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    f = plugins / "clock.py"
    f.write_text("# stub")
    mark, issue = classify_plugin_path(f, plugins)
    assert mark == INSPECTED
    assert issue is None


def test_classify_path_subdir_form_is_inspected(tmp_path):
    plugins = tmp_path / "plugins"
    sub = plugins / "weatherbug"
    sub.mkdir(parents=True)
    server = sub / "server.py"
    server.write_text("# stub")
    mark, issue = classify_plugin_path(server, plugins)
    assert mark == INSPECTED
    assert issue is None


def test_classify_path_trusted_form_is_trusted(tmp_path):
    plugins = tmp_path / "plugins"
    sub = plugins / "_trusted" / "vendor_x"
    sub.mkdir(parents=True)
    server = sub / "server.py"
    server.write_text("# stub")
    mark, issue = classify_plugin_path(server, plugins)
    assert mark == TRUSTED
    assert issue is None


def test_classify_path_subdir_with_wrong_filename_refused(tmp_path):
    plugins = tmp_path / "plugins"
    sub = plugins / "weatherbug"
    sub.mkdir(parents=True)
    f = sub / "main.py"  # not server.py
    f.write_text("# stub")
    mark, issue = classify_plugin_path(f, plugins)
    assert mark == ""
    assert issue is not None
    assert issue.reason == REASON_NOT_INSPECTABLE_PATH


def test_classify_path_trusted_flat_form_refused(tmp_path):
    """`plugins/_trusted/<name>.py` (flat-inside-trusted) is NOT a conforming
    layout — trusted plugins must use the subdir form so the user can
    inspect a folder, not just a file."""
    plugins = tmp_path / "plugins"
    (plugins / "_trusted").mkdir(parents=True)
    f = plugins / "_trusted" / "vendor_x.py"
    f.write_text("# stub")
    mark, issue = classify_plugin_path(f, plugins)
    assert mark == ""
    assert issue is not None
    assert issue.reason == REASON_NOT_INSPECTABLE_PATH


def test_classify_path_nested_subdir_refused(tmp_path):
    """`plugins/<a>/<b>/server.py` is non-conforming — only one level deep."""
    plugins = tmp_path / "plugins"
    nested = plugins / "outer" / "inner"
    nested.mkdir(parents=True)
    server = nested / "server.py"
    server.write_text("# stub")
    mark, issue = classify_plugin_path(server, plugins)
    assert mark == ""
    assert issue is not None
    assert issue.reason == REASON_NOT_INSPECTABLE_PATH


# ---------------------------------------------------------------------------
# Slice 3 — orchestrator scan on the flat layout
# ---------------------------------------------------------------------------


def test_discover_refuses_flat_plugin_with_forbidden_pattern(tmp_path):
    (tmp_path / "evil.py").write_text(
        _bad_plugin_source("evil", "import os\nos.system('echo got_in')"),
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "evil"
    assert err["reason"] == REASON_FORBIDDEN_PATTERN
    assert "os.system" in err["detail"]
    assert orc.inspectability_for("evil") is None


def test_discover_does_not_import_module_when_scan_fails(tmp_path):
    """The scan runs BEFORE import so import-time side effects never fire
    for refused plugins. Sentinel pattern: a top-level os.system() call that
    would create a file if it ran. The scan refuses, the side effect doesn't
    happen.

    We can't actually run `os.system` in the test (it would damage the host),
    so we use a sentinel that mutates a Python-level global at import time
    instead — combined with a forbidden pattern that would still trigger the
    scan first."""
    sentinel = tmp_path / "imported.txt"
    bad = textwrap.dedent(f"""
        from pathlib import Path
        Path({str(sentinel)!r}).write_text("imported")

        # Forbidden pattern below - scan must fire before the import side
        # effect above gets a chance.
        import os
        os.system('echo never')

        PLUGIN_NAME = "leaky"
        REQUIRED_CAPABILITIES = frozenset({{"clipboard"}})
        def create():
            class P:
                name = "leaky"
                def list_tools(self): return []
                async def call_tool(self, *a, **kw): pass
            return P()
    """)
    _write(tmp_path / "leaky.py", bad)
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert not sentinel.exists(), (
        "Import-time side effect leaked — scan must run before module exec"
    )
    assert orc.registration_errors[0]["reason"] == REASON_FORBIDDEN_PATTERN


def test_discover_accepts_clean_flat_plugin_with_inspected_mark(tmp_path):
    (tmp_path / "clean.py").write_text(_ok_plugin_source("clean"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert [t.name for t in orc.list_tools()] == ["ping"]
    assert orc.registration_errors == []
    assert orc.inspectability_for("clean") == INSPECTED


# ---------------------------------------------------------------------------
# Slice 4 — orchestrator scan on the subdir layout
# ---------------------------------------------------------------------------


def test_discover_refuses_subdir_plugin_with_forbidden_pattern(tmp_path):
    sub = tmp_path / "evilsub"
    sub.mkdir()
    (sub / "server.py").write_text(
        _bad_plugin_source("evilsub", "exec('print(1)')"),
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "server"  # path.stem of server.py
    assert err["reason"] == REASON_FORBIDDEN_PATTERN
    assert "exec" in err["detail"]


def test_discover_accepts_clean_subdir_plugin_with_inspected_mark(tmp_path):
    sub = tmp_path / "weatherbug"
    sub.mkdir()
    (sub / "server.py").write_text(_ok_plugin_source("weatherbug"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert "ping" in {t.name for t in orc.list_tools()}
    assert orc.inspectability_for("weatherbug") == INSPECTED


# ---------------------------------------------------------------------------
# Slice 5 — plugins/_trusted/ escape hatch
# ---------------------------------------------------------------------------


def test_trusted_plugin_skips_scan_and_loads_with_trusted_mark(tmp_path):
    """The same source that would be REFUSED under plugins/<name>/ LOADS
    under plugins/_trusted/<name>/ — and the orchestrator marks it
    TRUSTED so the tray renders the red badge."""
    trusted = tmp_path / "_trusted" / "vendor_x"
    trusted.mkdir(parents=True)
    # Same source that would trip the scan in the inspected layout.
    # `exec("")` is the canonical "would-fail-the-scan, no-ops at runtime"
    # bad line — the regex matches the text, the call evaluates to None.
    (trusted / "server.py").write_text(
        _bad_plugin_source("vendor_x", "exec('')"),
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    # `_bad_plugin_source` exposes a tool named "t".
    assert "t" in {t.name for t in orc.list_tools()}
    assert orc.inspectability_for("vendor_x") == TRUSTED
    # No registration_errors — the scan skipped, REQUIRED_CAPABILITIES OK.
    assert orc.registration_errors == []


def test_trusted_plugin_still_requires_required_capabilities(tmp_path):
    """Escape hatch bypasses the inspectability scan only — not the
    REQUIRED_CAPABILITIES declaration."""
    trusted = tmp_path / "_trusted" / "lazy_vendor"
    trusted.mkdir(parents=True)
    (trusted / "server.py").write_text(textwrap.dedent("""
        PLUGIN_NAME = "lazy_vendor"
        # No REQUIRED_CAPABILITIES on purpose.
        def create():
            class P:
                name = "lazy_vendor"
                def list_tools(self): return []
                async def call_tool(self, *a, **kw): pass
            return P()
    """))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "lazy_vendor"
    assert err["reason"] == REASON_MISSING


async def test_trusted_plugin_still_gates_at_call_time(tmp_path):
    """ADR-0005: trusted plugins still pass through the capability gate.
    A trusted plugin declaring shell_exec (deny-by-default) is registered
    but its tool refuses to fire when called with that capability."""
    trusted = tmp_path / "_trusted" / "vendor_shell"
    trusted.mkdir(parents=True)
    (trusted / "server.py").write_text(textwrap.dedent("""
        from cerebral.mcp.orchestrator import Tool, ToolResult
        PLUGIN_NAME = "vendor_shell"
        REQUIRED_CAPABILITIES = frozenset({"shell_exec"})
        class P:
            name = PLUGIN_NAME
            def list_tools(self):
                return [Tool(name="run", description="r", plugin=PLUGIN_NAME)]
            async def call_tool(self, tool_name, args):
                return ToolResult(content="should not reach plugin")
        def create():
            return P()
    """))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.inspectability_for("vendor_shell") == TRUSTED

    result = await orc.call_tool(
        "run", {}, capability=Capability.SHELL_EXEC,
    )
    assert result.is_error
    assert "shell_exec" in result.content


def test_trusted_subtree_with_no_server_py_records_non_conforming_path(tmp_path):
    trusted = tmp_path / "_trusted" / "halfbaked"
    trusted.mkdir(parents=True)
    # No server.py at all.
    (trusted / "README.md").write_text("missing server.py")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "halfbaked"
    assert err["reason"] == REASON_NOT_INSPECTABLE_PATH
    assert "halfbaked" in err["detail"]


# ---------------------------------------------------------------------------
# Slice 6 — non-conforming subdirs in plugins/ are recorded as path errors
# ---------------------------------------------------------------------------


def test_subdir_with_no_server_py_records_non_conforming_path(tmp_path):
    """A folder in plugins/ with no server.py used to be silently skipped.
    Post-#46 it surfaces to the tray as REASON_NOT_INSPECTABLE_PATH."""
    sub = tmp_path / "halfbaked"
    sub.mkdir()
    (sub / "main.py").write_text("# wrong filename")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "halfbaked"
    assert err["reason"] == REASON_NOT_INSPECTABLE_PATH


def test_underscored_dirs_other_than_trusted_are_silently_ignored(tmp_path):
    """`__pycache__/`, `_drafts/`, `_archive/` etc. are private scaffolding
    and must not pollute the registration_errors list."""
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "_drafts").mkdir()
    (tmp_path / "_drafts" / "wip.py").write_text("# WIP")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.registration_errors == []
    assert orc.list_tools() == []


def test_dotfile_dirs_are_silently_ignored(tmp_path):
    (tmp_path / ".vscode").mkdir()
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.registration_errors == []


def test_partial_refusal_leaves_good_plugins_intact(tmp_path):
    """Mixed bag: one clean flat, one tainted flat, one clean trusted, one
    bare subdir. Only the clean ones register; the rest record errors."""
    (tmp_path / "clean.py").write_text(_ok_plugin_source("clean"))
    (tmp_path / "tainted.py").write_text(
        _bad_plugin_source("tainted", "import os\nos.popen('x')"),
    )
    trusted = tmp_path / "_trusted" / "vendor"
    trusted.mkdir(parents=True)
    (trusted / "server.py").write_text(
        _bad_plugin_source("vendor", "exec('')"),
    )
    bare = tmp_path / "bare"
    bare.mkdir()

    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert {p for p in orc._plugins} == {"clean", "vendor"}
    refusal_names = {e["plugin_name"] for e in orc.registration_errors}
    assert "tainted" in refusal_names
    assert "bare" in refusal_names
    assert orc.inspectability_for("clean") == INSPECTED
    assert orc.inspectability_for("vendor") == TRUSTED


# ---------------------------------------------------------------------------
# Slice 7 — non-text plugin files surface REASON_NON_TEXT
# ---------------------------------------------------------------------------


def test_non_utf8_flat_plugin_recorded_as_non_text(tmp_path):
    """A file with bytes that aren't valid UTF-8 can't be scanned. The
    orchestrator refuses with REASON_NON_TEXT — does NOT fall through to
    import. (ADR-0005 inspectability AC: 'it is text Python …'.)"""
    bogus = tmp_path / "bogus.py"
    bogus.write_bytes(b"\xff\xfe\x00not_utf8\x00")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    assert orc.list_tools() == []
    err = orc.registration_errors[0]
    assert err["plugin_name"] == "bogus"
    assert err["reason"] == REASON_NON_TEXT


# ---------------------------------------------------------------------------
# Slice 8 — inspectability_for accessor
# ---------------------------------------------------------------------------


def test_inspectability_for_returns_none_for_direct_register():
    """Plugins registered via the direct register() path (tests, the parked
    builder) bypass disk discovery and have no inspectability mark."""
    from unittest.mock import AsyncMock, MagicMock
    from cerebral.mcp.orchestrator import Tool, ToolResult
    plugin = MagicMock()
    plugin.name = "direct"
    plugin.list_tools.return_value = [Tool(name="t", description="t", plugin="direct")]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    orc = MCPOrchestrator()
    orc.register(plugin)
    assert orc.inspectability_for("direct") is None


def test_inspectability_for_returns_none_for_unknown_plugin():
    orc = MCPOrchestrator()
    assert orc.inspectability_for("does_not_exist") is None


def test_unregister_clears_inspectability(tmp_path):
    (tmp_path / "clean.py").write_text(_ok_plugin_source("clean"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.inspectability_for("clean") == INSPECTED
    orc.unregister("clean")
    assert orc.inspectability_for("clean") is None


# ---------------------------------------------------------------------------
# Slice 9 — every real plugin module passes the canonical scan
# (parallel to Slice 10 of #44 — real-plugin audit, but for inspectability)
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGINS_DIR = _REPO_ROOT / "plugins"
_PLUGIN_FILES = sorted(
    p for p in _PLUGINS_DIR.glob("*.py") if not p.name.startswith("_")
)


@pytest.mark.parametrize(
    "plugin_path", _PLUGIN_FILES, ids=lambda p: p.stem,
)
def test_every_shipped_plugin_passes_the_canonical_scan(plugin_path):
    """All 32 shipped plugins must survive the inspectability scan.
    If this fails after a plugin edit, either the edit needs a different
    approach or the plugin belongs in plugins/_trusted/."""
    source = plugin_path.read_text(encoding="utf-8")
    issue = scan_source(source)
    assert issue is None, (
        f"{plugin_path.name} would be refused: {issue.detail if issue else ''}"
    )


# ---------------------------------------------------------------------------
# Slice 10 — builder consumes the canonical list (no duplication)
# ---------------------------------------------------------------------------


def test_builder_does_not_redefine_forbidden_patterns():
    """The canonical pattern list lives in cerebral.security.inspectability.
    The builder must NOT carry its own copy — that's the AC's 'no
    duplication between builder and orchestrator' requirement."""
    import plugins.builder as builder_module
    assert not hasattr(builder_module, "_FORBIDDEN_PATTERNS"), (
        "plugins.builder._FORBIDDEN_PATTERNS still defined — Issue #46 "
        "requires it to be sourced from cerebral.security.inspectability."
    )


def test_builder_scan_uses_canonical_patterns():
    """Sanity: feeding a forbidden pattern through the builder's scan helper
    still rejects, proving it's reading the canonical list."""
    from plugins.builder import BuilderPlugin
    bad = textwrap.dedent("""
        PLUGIN_NAME = "x"
        REQUIRED_CAPABILITIES = frozenset()
        import pickle
        pickle.loads(b'')
        def create():
            pass
    """)
    ok, reason = BuilderPlugin._scan_generated_code(bad)
    assert not ok
    assert "pickle" in reason


# ---------------------------------------------------------------------------
# Slice 11 — plugins_list IPC payload exposes the inspectability mark
# ---------------------------------------------------------------------------


def _build_plugins_list_payload(orc):
    """Mirror of cerebral.main._plugins_list_event — kept inline so the test
    pins the exact shape the tray reads without importing main.py (which
    initialises a lot of process-level state)."""
    registered = []
    for plugin_name in sorted(orc._plugins):
        caps = orc.required_capabilities_for(plugin_name)
        registered.append({
            "name": plugin_name,
            "required_capabilities": sorted(caps) if caps is not None else None,
            "inspectability": orc.inspectability_for(plugin_name),
        })
    return {
        "type": "plugins_list",
        "data": {
            "plugins": registered,
            "errors": orc.registration_errors,
        },
    }


def test_plugins_list_payload_includes_inspectability_field(tmp_path):
    (tmp_path / "clean.py").write_text(_ok_plugin_source("clean"))
    trusted = tmp_path / "_trusted" / "vendor"
    trusted.mkdir(parents=True)
    (trusted / "server.py").write_text(_ok_plugin_source("vendor"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    payload = _build_plugins_list_payload(orc)
    by_name = {p["name"]: p for p in payload["data"]["plugins"]}
    assert by_name["clean"]["inspectability"] == INSPECTED
    assert by_name["vendor"]["inspectability"] == TRUSTED


def test_plugins_list_payload_errors_carry_forbidden_pattern_reason(tmp_path):
    (tmp_path / "evil.py").write_text(
        _bad_plugin_source("evil", "import os\nos.system('x')"),
    )
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    payload = _build_plugins_list_payload(orc)
    assert payload["data"]["plugins"] == []
    refused = payload["data"]["errors"][0]
    assert refused["reason"] == REASON_FORBIDDEN_PATTERN
    assert refused["plugin_name"] == "evil"


def test_actual_main_payload_helper_includes_inspectability_field(tmp_path):
    """Belt-and-suspenders for the inlined shape mirror above — exercise
    the *real* cerebral.main._plugins_list_event by swapping its module-
    level `_orc` for our test orchestrator."""
    (tmp_path / "clean.py").write_text(_ok_plugin_source("clean"))
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    import cerebral.main as main_mod
    saved = main_mod._orc
    main_mod._orc = orc
    try:
        payload = main_mod._plugins_list_event()
    finally:
        main_mod._orc = saved

    by_name = {p["name"]: p for p in payload["data"]["plugins"]}
    assert by_name["clean"]["inspectability"] == INSPECTED
