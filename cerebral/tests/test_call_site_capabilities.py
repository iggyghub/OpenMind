"""
AST-completeness check — Issue #47, ADR-0005.

Covers:
  Slice 1 — happy paths: declared-complete and over-declared sources pass.
  Slice 2 — under-declaration is flagged with a clear, structured Finding.
  Slice 3 — dotted-target table coverage (subprocess family, fs writes/
            deletes, http libs, pyperclip, mss, pyautogui, keyring).
  Slice 4 — method-name fallback (Path.read_text / write_text / unlink /
            mkdir / touch).
  Slice 5 — alias indirection (module-level `_run_subprocess = subprocess.run`
            and instance attr `self._run_fn = run_fn or subprocess.run`).
  Slice 6 — dynamic dispatch (getattr, subscript, call-result) classifies
            as "unknown" and does not fail.
  Slice 7 — coverage shape: nested functions, lambdas, decorators, default
            args, comprehensions all walked.
  Slice 8 — multi-finding error message lists every site.
  Slice 9 — format_findings + CompletenessError formatting; assert_complete
            raises only on findings.
  Slice 10 — real-plugin audit: every shipped plugin's check_completeness
             call returns an empty findings tuple (over-declaration is fine,
             under-declaration is the only failure mode).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.security import (
    Capability,
    CompletenessError,
    Finding,
    REASON_UNDER_DECLARED,
    assert_complete,
    check_completeness,
    format_findings,
)
from cerebral.security.call_site_capabilities import (
    DOTTED_TARGETS,
    METHOD_NAMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SCAFFOLD = textwrap.dedent("""
    PLUGIN_NAME = "p"
    REQUIRED_CAPABILITIES = frozenset()

    {body}

    def create():
        return None
""")


def _plugin_with(body: str) -> str:
    """A minimal plugin scaffold with the supplied body inserted at module
    level (so the walker hits its calls). Used as the input to
    ``check_completeness`` directly — no disk, no orchestrator.

    The body is dedented separately and substituted into a pre-dedented
    scaffold so f-string substitution doesn't break dedent's common-prefix
    detection.
    """
    return _SCAFFOLD.replace("{body}", textwrap.dedent(body))


# ---------------------------------------------------------------------------
# Slice 1 — happy paths
# ---------------------------------------------------------------------------


def test_empty_source_passes():
    assert check_completeness("", frozenset()) == ()


def test_no_calls_passes():
    src = "x = 1\ny = x + 2\n"
    assert check_completeness(src, frozenset()) == ()


def test_declared_complete_passes():
    src = _plugin_with(textwrap.dedent("""
        import shutil
        def cleanup(p):
            shutil.rmtree(p)
    """))
    assert check_completeness(src, frozenset({"fs_delete"})) == ()


def test_over_declared_passes():
    """Over-declaration is fine — the walker only flags under-declaration."""
    src = _plugin_with(textwrap.dedent("""
        import shutil
        def cleanup(p):
            shutil.rmtree(p)
    """))
    # Plugin declares more than it needs: walker accepts.
    assert check_completeness(
        src, frozenset({"fs_delete", "fs_write", "shell_exec"}),
    ) == ()


# ---------------------------------------------------------------------------
# Slice 2 — under-declaration: clear, structured Finding
# ---------------------------------------------------------------------------


def test_under_declared_fails_with_finding():
    src = _plugin_with(textwrap.dedent("""
        import shutil
        def cleanup(p):
            shutil.rmtree(p)
    """))
    findings = check_completeness(src, frozenset({"fs_read"}))
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, Finding)
    assert f.target == "shutil.rmtree"
    assert f.required == frozenset({Capability.FS_DELETE})
    assert f.declared == frozenset({"fs_read"})
    assert "shutil.rmtree" in f.snippet


def test_finding_carries_line_and_col():
    src = _plugin_with(textwrap.dedent("""
        import os
        def f():
            os.remove("/tmp/x")
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    f = findings[0]
    assert f.line > 0
    assert f.col >= 0
    assert "os.remove" in f.snippet


def test_failure_message_includes_path_line_and_required():
    src = _plugin_with(textwrap.dedent("""
        import shutil
        def f(p):
            shutil.rmtree(p)
    """))
    findings = check_completeness(
        src, frozenset({"fs_read"}), source_path="plugins/myplugin.py",
    )
    msg = format_findings(findings, source_path="plugins/myplugin.py")
    assert "plugins/myplugin.py" in msg
    assert "shutil.rmtree" in msg
    assert "fs_delete" in msg
    # Declared set shown for context.
    assert "fs_read" in msg


# ---------------------------------------------------------------------------
# Slice 3 — dotted-target table coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call_expr, undeclared_required",
    [
        # fs_delete
        ("os.remove('/tmp/x')", "fs_delete"),
        ("os.unlink('/tmp/x')", "fs_delete"),
        ("os.rmdir('/tmp/d')", "fs_delete"),
        ("shutil.rmtree('/tmp/d')", "fs_delete"),
        # fs_write
        ("shutil.copy('a', 'b')", "fs_write"),
        ("shutil.copytree('a', 'b')", "fs_write"),
        ("os.makedirs('/tmp/x')", "fs_write"),
        ("shutil.move('a', 'b')", "fs_write"),
        # clipboard
        ("pyperclip.copy('x')", "clipboard"),
        ("pyperclip.paste()", "clipboard"),
        # screen capture
        ("mss.mss()", "screen_capture"),
        ("ImageGrab.grab()", "screen_capture"),
        # device control
        ("pyautogui.click(0, 0)", "device_control"),
        ("pyautogui.typewrite('hi')", "device_control"),
        ("keyboard.write('hi')", "device_control"),
        # secrets
        ("keyring.get_password('s', 'u')", "secrets_read"),
        # code install
        ("pip.main(['install', 'x'])", "code_install"),
    ],
)
def test_dotted_target_under_declared_is_flagged(call_expr, undeclared_required):
    src = _plugin_with(call_expr)
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1, f"{call_expr} produced {findings!r}"
    required_values = {c.value for c in findings[0].required}
    assert undeclared_required in required_values


def test_subprocess_run_requires_any_shell_family_class():
    """`subprocess.*` calls map to an any-of family — declaring any one of
    the listed semantic classes satisfies the check. Catches the case where
    a plugin shells out but declares nothing."""
    src = _plugin_with("subprocess.run(['ls'])")
    # Under-declared: no shell-family class.
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    required = findings[0].required
    assert Capability.SHELL_EXEC in required
    assert Capability.DEVICE_CONTROL in required
    assert Capability.NETWORK_RECON in required
    assert Capability.CODE_INSTALL in required


def test_subprocess_satisfied_by_device_control_alone():
    """docker.py / system.py / printer.py — declare device_control, shell
    out via subprocess. The walker accepts because device_control is in
    the any-of family for subprocess calls."""
    src = _plugin_with("subprocess.run(['docker', 'ps'])")
    assert check_completeness(src, frozenset({"device_control"})) == ()


def test_subprocess_satisfied_by_network_recon_alone():
    """network_scanner — declares network_recon, shells out to nmap."""
    src = _plugin_with("subprocess.Popen(['nmap', '-p', '80', 'host'])")
    assert check_completeness(src, frozenset({"network_recon"})) == ()


def test_http_libs_any_network_egress_class_satisfies():
    src_local = _plugin_with("httpx.get('http://localhost/x')")
    assert check_completeness(src_local, frozenset({"network_egress_local"})) == ()
    src_cloud = _plugin_with("httpx.get('http://example.com/x')")
    assert check_completeness(src_cloud, frozenset({"network_egress_cloud"})) == ()


def test_http_lib_with_no_network_declaration_fails():
    """The any-of doesn't mean "no declaration needed" — declaring nothing
    in the network family still fails."""
    src = _plugin_with("requests.get('http://example.com')")
    findings = check_completeness(src, frozenset({"fs_read"}))
    assert len(findings) == 1
    assert findings[0].required == frozenset({
        Capability.NETWORK_EGRESS_LOCAL,
        Capability.NETWORK_EGRESS_CLOUD,
    })


# ---------------------------------------------------------------------------
# Slice 4 — method-name fallback (Path.* and other receiver-typed methods)
# ---------------------------------------------------------------------------


def test_path_read_text_requires_fs_read():
    src = _plugin_with(textwrap.dedent("""
        from pathlib import Path
        def f():
            return Path('x').read_text()
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].required == frozenset({Capability.FS_READ})


def test_path_write_text_requires_fs_write():
    src = _plugin_with(textwrap.dedent("""
        from pathlib import Path
        def f():
            Path('x').write_text('hi')
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].required == frozenset({Capability.FS_WRITE})


def test_path_unlink_requires_fs_delete():
    src = _plugin_with(textwrap.dedent("""
        from pathlib import Path
        def f():
            Path('x').unlink()
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].required == frozenset({Capability.FS_DELETE})


def test_path_touch_mkdir_require_fs_write():
    src = _plugin_with(textwrap.dedent("""
        from pathlib import Path
        def f():
            Path('x').touch()
            Path('y').mkdir()
    """))
    findings = check_completeness(src, frozenset({"fs_read"}))
    assert len(findings) == 2
    for f in findings:
        assert f.required == frozenset({Capability.FS_WRITE})


def test_bare_open_requires_fs_read():
    """Raw `open(..., 'w')` is a #46 hard-fail; bare `open(...)` (no mode
    or read mode) the walker treats as fs_read."""
    src = _plugin_with(textwrap.dedent("""
        def f():
            with open('/etc/hosts') as fh:
                return fh.read()
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].required == frozenset({Capability.FS_READ})


# ---------------------------------------------------------------------------
# Slice 5 — alias indirection (the #46-era pattern that all shell-touching
# shipped plugins use)
# ---------------------------------------------------------------------------


def test_module_level_alias_resolves_to_target():
    """`_run_subprocess = subprocess.run` (the form in plugins/builder.py).
    A call to the alias must be classified as the target's capability."""
    src = textwrap.dedent("""
        import subprocess
        _run_subprocess = subprocess.run

        def doit(cmd):
            return _run_subprocess(cmd)
    """)
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "subprocess.run"
    # Subprocess family — declaring any shell-able class satisfies.
    assert Capability.SHELL_EXEC in findings[0].required


def test_module_level_alias_satisfied_by_target_capability():
    src = textwrap.dedent("""
        import subprocess
        _run_subprocess = subprocess.run

        def doit(cmd):
            return _run_subprocess(cmd)
    """)
    assert check_completeness(src, frozenset({"code_install"})) == ()


def test_init_self_attribute_alias_simple():
    """`self._run = subprocess.run` (direct binding, no `or`)."""
    src = textwrap.dedent("""
        import subprocess

        class P:
            def __init__(self):
                self._run = subprocess.run
            def doit(self, cmd):
                return self._run(cmd)
    """)
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "subprocess.run"


def test_init_self_attribute_or_chain_resolves_to_subprocess_run():
    """`self._run_fn = run_fn or subprocess.run` — the pattern in
    shell.py, docker.py, git.py, system.py, ssh.py, vpn.py, bitwarden.py,
    printer.py, package_manager.py, network_scanner.py."""
    src = textwrap.dedent("""
        import subprocess

        class P:
            def __init__(self, run_fn=None):
                self._run_fn = run_fn or subprocess.run
            def doit(self, argv):
                return self._run_fn(argv, capture_output=True, text=True)
    """)
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "subprocess.run"


def test_init_alias_or_chain_satisfied_by_declared_class():
    """The whole point: shell.py declares shell_exec, docker.py declares
    device_control. The alias-resolved walker accepts both because the
    subprocess any-of family includes both."""
    src = textwrap.dedent("""
        import subprocess

        class P:
            def __init__(self, run_fn=None):
                self._run_fn = run_fn or subprocess.run
            def doit(self, argv):
                return self._run_fn(argv)
    """)
    assert check_completeness(src, frozenset({"shell_exec"})) == ()
    assert check_completeness(src, frozenset({"device_control"})) == ()


def test_incidental_self_attribute_alias_does_not_capture():
    """`self._fetch = fetch_fn or _default_fetch` — `_default_fetch` is a
    local function, not a known target. The collector must NOT record it
    as an alias. Calls inside `_default_fetch` itself are caught when the
    walker recurses into its body."""
    src = textwrap.dedent("""
        async def _default_fetch(url):
            import httpx
            return await httpx.get(url)

        class P:
            def __init__(self, fetch_fn=None):
                self._fetch = fetch_fn or _default_fetch
            async def doit(self, url):
                return await self._fetch(url)
    """)
    # The walker DOES catch the call inside _default_fetch (httpx.get)
    # because it walks the whole module. self._fetch(...) is not aliased.
    findings = check_completeness(src, frozenset())
    # exactly one finding from httpx.get inside _default_fetch
    assert any("httpx.get" in f.target for f in findings)
    # All findings are the http-libs any-of network egress.
    for f in findings:
        assert f.required == frozenset({
            Capability.NETWORK_EGRESS_LOCAL,
            Capability.NETWORK_EGRESS_CLOUD,
        })


# ---------------------------------------------------------------------------
# Slice 6 — dynamic dispatch classifies as unknown, never fails
# ---------------------------------------------------------------------------


def test_getattr_dispatch_is_unknown():
    src = _plugin_with("getattr(__builtins__, 'open')('x')")
    assert check_completeness(src, frozenset()) == ()


def test_globals_subscript_dispatch_is_unknown():
    src = _plugin_with("globals()['shutil'].rmtree('/tmp/x')")
    # `globals()['shutil']` is a Subscript on a Call — non-dotted; ignored.
    assert check_completeness(src, frozenset()) == ()


def test_call_result_chain_is_unknown():
    """`get_handler().rmtree(...)` — receiver is a Call, can't dot-resolve."""
    src = _plugin_with(textwrap.dedent("""
        def get_handler():
            import shutil
            return shutil
        def cleanup(p):
            get_handler().rmtree(p)
    """))
    findings = check_completeness(src, frozenset())
    # The `get_handler()` call itself is a Name not in any table, fine.
    # The `.rmtree(...)` is a Call whose receiver is a Call — dot-resolution
    # returns None; classified as unknown.
    assert findings == ()


# ---------------------------------------------------------------------------
# Slice 7 — coverage shape: nested funcs, lambdas, decorators, default args
# ---------------------------------------------------------------------------


def test_nested_function_call_is_detected():
    src = _plugin_with(textwrap.dedent("""
        def outer():
            def inner(p):
                import shutil
                shutil.rmtree(p)
            return inner
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "shutil.rmtree"


def test_lambda_call_is_detected():
    src = _plugin_with(textwrap.dedent("""
        import shutil
        delete = lambda p: shutil.rmtree(p)
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "shutil.rmtree"


def test_decorator_call_is_detected():
    """A call-form decorator like `@register_thing(...)` is itself a Call;
    if its target is in the table the walker catches it. Use a synthetic
    table-hit so the test isn't tied to a specific real decorator."""
    src = _plugin_with(textwrap.dedent("""
        @pyperclip.copy('marker')
        def some_func():
            pass
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "pyperclip.copy"


def test_default_arg_call_is_detected():
    src = _plugin_with(textwrap.dedent("""
        import os
        def f(p=os.remove('/tmp/x')):
            return p
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "os.remove"


def test_comprehension_call_is_detected():
    src = _plugin_with(textwrap.dedent("""
        import os
        def f(paths):
            return [os.remove(p) for p in paths]
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "os.remove"


def test_class_body_call_is_detected():
    src = _plugin_with(textwrap.dedent("""
        import pyperclip
        class C:
            VALUE = pyperclip.paste()
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 1
    assert findings[0].target == "pyperclip.paste"


# ---------------------------------------------------------------------------
# Slice 8 — multi-finding error lists every site
# ---------------------------------------------------------------------------


def test_multi_finding_lists_all_sites():
    src = _plugin_with(textwrap.dedent("""
        import shutil, os
        def f(a, b):
            shutil.rmtree(a)
            os.remove(b)
            pyperclip.copy('x')
    """))
    findings = check_completeness(src, frozenset())
    assert len(findings) == 3
    targets = {f.target for f in findings}
    assert targets == {"shutil.rmtree", "os.remove", "pyperclip.copy"}


def test_multi_finding_format_emits_one_line_per_finding():
    src = _plugin_with(textwrap.dedent("""
        import shutil, os
        def f(a, b):
            shutil.rmtree(a)
            os.remove(b)
    """))
    findings = check_completeness(src, frozenset())
    msg = format_findings(findings, source_path="plugins/p.py")
    # Header + 2 finding lines.
    lines = msg.splitlines()
    assert lines[0].startswith("AST completeness check failed")
    assert sum(1 for ln in lines if "plugins/p.py:" in ln) == 2


# ---------------------------------------------------------------------------
# Slice 9 — assert_complete + CompletenessError
# ---------------------------------------------------------------------------


def test_assert_complete_no_findings_returns_none():
    src = _plugin_with("import os")  # no offending calls
    assert assert_complete(src, frozenset()) is None


def test_assert_complete_raises_on_finding():
    src = _plugin_with(textwrap.dedent("""
        import shutil
        def f(p):
            shutil.rmtree(p)
    """))
    with pytest.raises(CompletenessError) as excinfo:
        assert_complete(src, frozenset(), source_path="plugins/p.py")
    err = excinfo.value
    assert len(err.findings) == 1
    assert err.findings[0].target == "shutil.rmtree"
    assert err.source_path == "plugins/p.py"
    # str(err) is the formatted message.
    assert "shutil.rmtree" in str(err)
    assert "plugins/p.py" in str(err)


def test_completeness_error_carries_structured_findings():
    """The builder consumes findings directly to surface gaps in its own
    error format. Keep the public attribute shape stable."""
    src = _plugin_with(textwrap.dedent("""
        import os
        def f():
            os.remove('x')
    """))
    findings = check_completeness(src, frozenset())
    err = CompletenessError(findings, source_path="x.py")
    assert err.findings == findings
    assert err.source_path == "x.py"


def test_reason_code_is_stable():
    """Refusal reason code follows the REASON_* style of #44/#46 — stable
    string for tray rendering / log filtering."""
    assert REASON_UNDER_DECLARED == "under_declared_capability"


# ---------------------------------------------------------------------------
# Slice 10 — real-plugin audit: every shipped plugin passes
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGINS_DIR = _REPO_ROOT / "plugins"


def _shipped_plugin_sources() -> list[tuple[str, str, frozenset[str]]]:
    """Return (id, source, declared) for every shipped plugin module.

    Mirrors discovery layout: flat `plugins/<name>.py` files. Subdir-form
    plugins (`plugins/<name>/server.py`) aren't shipped yet but if any
    appear they get included automatically.
    """
    out: list[tuple[str, str, frozenset[str]]] = []
    for path in sorted(_PLUGINS_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        ns: dict = {}
        # Cheap declaration extraction: pull REQUIRED_CAPABILITIES out
        # without actually executing the plugin (which would import its
        # third-party deps). The constant is always a literal frozenset
        # of string literals — eval is fine in test context.
        import ast as _ast
        tree = _ast.parse(src)
        declared: frozenset[str] = frozenset()
        for node in tree.body:
            if (
                isinstance(node, _ast.AnnAssign)
                and isinstance(node.target, _ast.Name)
                and node.target.id == "REQUIRED_CAPABILITIES"
                and node.value is not None
            ):
                declared = frozenset(_eval_capabilities_literal(node.value))
            elif (
                isinstance(node, _ast.Assign)
                and any(
                    isinstance(t, _ast.Name) and t.id == "REQUIRED_CAPABILITIES"
                    for t in node.targets
                )
                and node.value is not None
            ):
                declared = frozenset(_eval_capabilities_literal(node.value))
        out.append((path.stem, src, declared))
    for sub in sorted(_PLUGINS_DIR.glob("*/server.py")):
        if sub.parts[-2].startswith("_"):
            continue
        src = sub.read_text(encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(src)
        declared = frozenset()
        for node in tree.body:
            if (
                isinstance(node, _ast.AnnAssign)
                and isinstance(node.target, _ast.Name)
                and node.target.id == "REQUIRED_CAPABILITIES"
                and node.value is not None
            ):
                declared = frozenset(_eval_capabilities_literal(node.value))
        out.append((sub.parts[-2], src, declared))
    return out


def _eval_capabilities_literal(value_node) -> tuple[str, ...]:
    """Pull strings out of a literal `frozenset({"a", "b"})` AST node."""
    import ast as _ast
    if isinstance(value_node, _ast.Call):
        # frozenset({...}) or frozenset([...]) — first positional arg is the set/list/tuple
        if value_node.args:
            inner = value_node.args[0]
            if isinstance(inner, (_ast.Set, _ast.List, _ast.Tuple)):
                return tuple(
                    elt.value for elt in inner.elts
                    if isinstance(elt, _ast.Constant) and isinstance(elt.value, str)
                )
        return ()
    if isinstance(value_node, (_ast.Set, _ast.List, _ast.Tuple)):
        return tuple(
            elt.value for elt in value_node.elts
            if isinstance(elt, _ast.Constant) and isinstance(elt.value, str)
        )
    return ()


_SHIPPED = _shipped_plugin_sources()


@pytest.mark.parametrize(
    "plugin_id, source, declared",
    _SHIPPED,
    ids=[name for name, _, _ in _SHIPPED],
)
def test_shipped_plugin_passes_ast_completeness(plugin_id, source, declared):
    """Every shipped plugin's declared capabilities cover every reachable
    call site the walker can statically classify. Under-declaration would
    fail here; over-declaration is fine and intentional in most cases."""
    findings = check_completeness(
        source, declared, source_path=f"plugins/{plugin_id}",
    )
    assert findings == (), format_findings(
        findings, source_path=f"plugins/{plugin_id}",
    )


# ---------------------------------------------------------------------------
# Sanity: tables expose the expected shape
# ---------------------------------------------------------------------------


def test_dotted_targets_table_uses_capability_enum_values():
    for target, req in DOTTED_TARGETS.items():
        assert isinstance(req, frozenset), target
        assert all(isinstance(c, Capability) for c in req), target
        assert len(req) >= 1, target


def test_method_names_table_uses_capability_enum_values():
    for name, req in METHOD_NAMES.items():
        assert isinstance(req, frozenset), name
        assert all(isinstance(c, Capability) for c in req), name
