"""
AST-completeness check for declared capabilities — Issue #47, ADR-0005.

Static-pattern inspectability (#46) catches the obviously-dangerous primitives
in plugin source (raw ``subprocess.*``, ``exec``, ``eval``, …). It does not
catch *under-declaration*: a plugin that imports ``shutil`` and calls
``shutil.rmtree(...)`` but only declares ``fs_read`` in
``REQUIRED_CAPABILITIES`` — clean against the static scan, but missing the
``fs_delete`` declaration.

This module walks a plugin's AST and verifies every call site whose target
requires a capability has that capability declared. It is mandatory for
builder-generated plugins (the builder consumes the report and refuses to
persist the staged source on failure) and exposed as a callable utility for
hand-authored plugins (the author's hand-typed declaration is the sign-off,
so registration does not run the check).

Single-file walk. For ``plugins/<name>/server.py`` only ``server.py`` is
walked — sibling files in the same package directory are out of v1 scope.
Dynamic-dispatch call forms (``getattr(...)``, ``globals()["name"]``,
``__import__(...)`` results) classify as "unknown" and do not fail the
check; #46's static-pattern scan deters the most dangerous forms.

Aliases are followed one hop. Plugin source uses two conventions to keep
``subprocess.*`` out of the literal call form (so #46's regex passes):

  - Module-level alias: ``_run_subprocess = subprocess.run`` (in
    plugins/builder.py).
  - Instance attribute, often via an ``or`` chain:
    ``self._run_fn = run_fn or subprocess.run`` (in shell.py, docker.py,
    git.py, system.py, and others).

The collector picks up both forms before the call walk so
``self._run_fn(...)`` resolves to its target and is classified accordingly.

# Capability-requirement semantics

Every call-site rule maps to a ``frozenset[Capability]`` — the *any-of* set
that satisfies the call. A plugin passes the rule iff its
``REQUIRED_CAPABILITIES`` contains **at least one** of the listed classes.
That handles two real cases cleanly:

  - **Unambiguous calls** like ``shutil.rmtree(...)`` map to
    ``frozenset({FS_DELETE})`` — single-class requirement, must be
    declared.
  - **Ambiguous calls** like ``subprocess.run(...)`` map to a set of
    "any of the classes that can be accomplished with a subprocess"
    (``SHELL_EXEC``, ``DEVICE_CONTROL``, ``NETWORK_RECON``,
    ``NETWORK_CONFIG``, ``CODE_INSTALL``, ``FS_WRITE``, ``FS_DELETE``).
    The shipped plugins shell out for legitimate semantic intents
    (docker → device_control, network_scanner → network_recon, etc.);
    the plugin author's declared class is the intent record and the
    walker just verifies they declared *something* in the family.
    Catches the case where a plugin shells out but declares nothing.

The same any-of treatment applies to HTTP client libraries: literal-URL
local/cloud splitting via static analysis is unreliable when the URL is
constructed from a runtime base (the ``n8n``, ``phone``, and ``browser``
plugins all use a configurable ``base_url`` that defaults to localhost),
so v1 maps HTTP calls to ``{NETWORK_EGRESS_LOCAL, NETWORK_EGRESS_CLOUD}``
and accepts either. A future deepening can tighten this.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterable

from cerebral.security.gate import Capability

# Refusal reason code — same string style as inspectability.REASON_*.
REASON_UNDER_DECLARED = "under_declared_capability"


# ---------------------------------------------------------------------------
# Call-site → capability tables.
#
# DOTTED_TARGETS: fully-qualified dotted form (after alias resolution) →
#                 the any-of set of capability classes that satisfy the call.
# METHOD_NAMES:   bare attribute name (used when the receiver type can't be
#                 resolved, e.g. ``some_path.read_text()``) → same.
# ---------------------------------------------------------------------------

# Any of these declared classes satisfies a `subprocess.*` call. Subprocess
# is a versatile primitive — the shipped plugins use it for shelling out to
# docker, nmap, ufw, pip, bitwarden CLI, git, OS commands, etc. — and the
# plugin's intent-level capability declaration (issue #44) records *which*
# of these the plugin actually does. The walker verifies the author wrote
# down at least one shell-able class; the runtime gate handles the rest.
_SHELL_FAMILY: frozenset[Capability] = frozenset({
    Capability.SHELL_EXEC,
    Capability.DEVICE_CONTROL,
    Capability.NETWORK_RECON,
    Capability.NETWORK_CONFIG,
    Capability.CODE_INSTALL,
    Capability.FS_WRITE,
    Capability.FS_DELETE,
    Capability.VAULT_UNLOCK,
    Capability.SECRETS_READ,
})

# Any-of HTTP/network egress — see module docstring for why URL splitting
# is deferred to a future deepening.
_NETWORK_EGRESS_ANY: frozenset[Capability] = frozenset({
    Capability.NETWORK_EGRESS_LOCAL,
    Capability.NETWORK_EGRESS_CLOUD,
})

# Raw socket use: also satisfied by NETWORK_RECON (network_scanner.py
# probes via socket.create_connection — that's recon, not egress in the
# user-facing sense). HTTP libs above are intentionally narrower because
# they imply real outbound traffic.
_SOCKET_FAMILY: frozenset[Capability] = frozenset({
    Capability.NETWORK_EGRESS_LOCAL,
    Capability.NETWORK_EGRESS_CLOUD,
    Capability.NETWORK_RECON,
})


def _single(cap: Capability) -> frozenset[Capability]:
    return frozenset({cap})


DOTTED_TARGETS: dict[str, frozenset[Capability]] = {
    # ----- subprocess (alias-resolved) -----
    "subprocess.run": _SHELL_FAMILY,
    "subprocess.Popen": _SHELL_FAMILY,
    "subprocess.call": _SHELL_FAMILY,
    "subprocess.check_call": _SHELL_FAMILY,
    "subprocess.check_output": _SHELL_FAMILY,
    # ----- filesystem delete/move -----
    "os.remove": _single(Capability.FS_DELETE),
    "os.unlink": _single(Capability.FS_DELETE),
    "os.rmdir": _single(Capability.FS_DELETE),
    "os.removedirs": _single(Capability.FS_DELETE),
    "shutil.rmtree": _single(Capability.FS_DELETE),
    "shutil.move": _single(Capability.FS_WRITE),
    # ----- filesystem write -----
    "shutil.copy": _single(Capability.FS_WRITE),
    "shutil.copy2": _single(Capability.FS_WRITE),
    "shutil.copyfile": _single(Capability.FS_WRITE),
    "shutil.copytree": _single(Capability.FS_WRITE),
    "os.makedirs": _single(Capability.FS_WRITE),
    # ----- clipboard -----
    "pyperclip.copy": _single(Capability.CLIPBOARD),
    "pyperclip.paste": _single(Capability.CLIPBOARD),
    # ----- HTTP / network egress (any-of, see docstring) -----
    "requests.get": _NETWORK_EGRESS_ANY,
    "requests.post": _NETWORK_EGRESS_ANY,
    "requests.put": _NETWORK_EGRESS_ANY,
    "requests.delete": _NETWORK_EGRESS_ANY,
    "requests.patch": _NETWORK_EGRESS_ANY,
    "requests.head": _NETWORK_EGRESS_ANY,
    "requests.request": _NETWORK_EGRESS_ANY,
    "httpx.get": _NETWORK_EGRESS_ANY,
    "httpx.post": _NETWORK_EGRESS_ANY,
    "httpx.put": _NETWORK_EGRESS_ANY,
    "httpx.delete": _NETWORK_EGRESS_ANY,
    "httpx.patch": _NETWORK_EGRESS_ANY,
    "httpx.head": _NETWORK_EGRESS_ANY,
    "httpx.request": _NETWORK_EGRESS_ANY,
    "httpx.AsyncClient": _NETWORK_EGRESS_ANY,
    "httpx.Client": _NETWORK_EGRESS_ANY,
    "aiohttp.ClientSession": _NETWORK_EGRESS_ANY,
    "aiohttp.request": _NETWORK_EGRESS_ANY,
    "urllib.request.urlopen": _NETWORK_EGRESS_ANY,
    "urllib.request.urlretrieve": _NETWORK_EGRESS_ANY,
    "socket.socket": _SOCKET_FAMILY,
    "socket.create_connection": _SOCKET_FAMILY,
    "socket.getaddrinfo": _SOCKET_FAMILY,
    # ----- screen capture -----
    "mss.mss": _single(Capability.SCREEN_CAPTURE),
    "PIL.ImageGrab.grab": _single(Capability.SCREEN_CAPTURE),
    "ImageGrab.grab": _single(Capability.SCREEN_CAPTURE),
    # ----- device control -----
    "pyautogui.click": _single(Capability.DEVICE_CONTROL),
    "pyautogui.write": _single(Capability.DEVICE_CONTROL),
    "pyautogui.press": _single(Capability.DEVICE_CONTROL),
    "pyautogui.hotkey": _single(Capability.DEVICE_CONTROL),
    "pyautogui.moveTo": _single(Capability.DEVICE_CONTROL),
    "pyautogui.typewrite": _single(Capability.DEVICE_CONTROL),
    "keyboard.press": _single(Capability.DEVICE_CONTROL),
    "keyboard.write": _single(Capability.DEVICE_CONTROL),
    "mouse.click": _single(Capability.DEVICE_CONTROL),
    "mouse.move": _single(Capability.DEVICE_CONTROL),
    # ----- secrets -----
    "keyring.get_password": _single(Capability.SECRETS_READ),
    "keyring.set_password": _single(Capability.SECRETS_READ),
    # ----- code install -----
    "pip.main": _single(Capability.CODE_INSTALL),
}


# Bare attribute names — fall back when the receiver type isn't statically
# known. Includes only names distinctive enough that the false-positive risk
# is low (Path.read_text / .write_text / .unlink / .touch / .mkdir, Tk
# clipboard_*). Generic names like `replace`, `rename`, `read`, `write` are
# deliberately *not* here — they collide with str/dict/StringIO/cursor and
# would flag innocent calls. The plugin author's hand-typed declaration
# remains the contract for those.
METHOD_NAMES: dict[str, frozenset[Capability]] = {
    # ----- pathlib.Path read methods -----
    "read_text": _single(Capability.FS_READ),
    "read_bytes": _single(Capability.FS_READ),
    "iterdir": _single(Capability.FS_READ),
    # ----- pathlib.Path write methods -----
    "write_text": _single(Capability.FS_WRITE),
    "write_bytes": _single(Capability.FS_WRITE),
    "touch": _single(Capability.FS_WRITE),
    "mkdir": _single(Capability.FS_WRITE),
    # ----- pathlib.Path delete -----
    "unlink": _single(Capability.FS_DELETE),
    # ----- clipboard via tkinter Tk() -----
    "clipboard_get": _single(Capability.CLIPBOARD),
    "clipboard_clear": _single(Capability.CLIPBOARD),
    "clipboard_append": _single(Capability.CLIPBOARD),
}


# Bare `open(...)` is FS_READ unless the mode is a write flag, in which
# case #46 already hard-fails before this walker runs (raw write open is a
# forbidden pattern). Treat the bare-name call as FS_READ; declared
# write-capable plugins will already over-declare with FS_WRITE for the
# Path.write_text path.
_BARE_NAMES: dict[str, frozenset[Capability]] = {
    "open": _single(Capability.FS_READ),
}


# ---------------------------------------------------------------------------
# Findings + entry points
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One under-declared call site discovered by the walker.

    ``required`` is the any-of set: declaring **any** of those classes
    satisfies this call. The error message formats it accordingly.
    """
    line: int
    col: int
    snippet: str
    target: str
    required: frozenset[Capability]
    declared: frozenset[str]


class CompletenessError(Exception):
    """Raised by ``assert_complete`` when one or more call sites under-declare.

    Carries the structured findings list so callers (the builder) can
    surface them in their own error format.
    """

    def __init__(
        self,
        findings: tuple[Finding, ...],
        *,
        source_path: str | None = None,
    ) -> None:
        self.findings = findings
        self.source_path = source_path
        super().__init__(format_findings(findings, source_path=source_path))


def check_completeness(
    source: str,
    declared: Iterable[str],
    *,
    source_path: str | None = None,
) -> tuple[Finding, ...]:
    """Walk ``source`` and return findings for every under-declared call site.

    ``declared`` is the plugin's hand-typed ``REQUIRED_CAPABILITIES``
    contents — string-form names from the 16-class vocabulary. Empty tuple
    return means the declaration covers every reachable call.

    A SyntaxError in ``source`` is raised verbatim — the caller is
    responsible for deciding what to do (the builder rejects, the
    hand-authored loader propagates).
    """
    declared_set: frozenset[str] = frozenset(declared)
    tree = ast.parse(source)
    source_lines = source.splitlines()

    aliases = _AliasCollector()
    aliases.visit(tree)

    visitor = _CallVisitor(
        module_aliases=aliases.module_aliases,
        self_aliases=aliases.self_aliases,
        declared=declared_set,
        source_lines=source_lines,
    )
    visitor.visit(tree)
    return tuple(visitor.findings)


def assert_complete(
    source: str,
    declared: Iterable[str],
    *,
    source_path: str | None = None,
) -> None:
    """Walk ``source`` and raise ``CompletenessError`` on any finding."""
    findings = check_completeness(source, declared, source_path=source_path)
    if findings:
        raise CompletenessError(findings, source_path=source_path)


def format_findings(
    findings: Iterable[Finding], *, source_path: str | None = None,
) -> str:
    """Render findings to the human-readable failure message.

    Format per finding::

        path:line:col: <snippet> requires <cap or any-of {a,b}> (declared: {...})

    Multi-finding errors emit one line per finding.
    """
    listed = list(findings)
    if not listed:
        return ""
    prefix = source_path or "<source>"
    lines = ["AST completeness check failed:"]
    for f in listed:
        if len(f.required) == 1:
            need = next(iter(f.required)).value
        else:
            need = "one of {" + ", ".join(
                sorted(c.value for c in f.required)
            ) + "}"
        declared_repr = (
            "{" + ", ".join(sorted(repr(d) for d in f.declared)) + "}"
            if f.declared else "frozenset()"
        )
        lines.append(
            f"  {prefix}:{f.line}:{f.col}: {f.snippet} requires {need} "
            f"(declared: {declared_repr})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal: dotted-name resolution + alias collection + call walker
# ---------------------------------------------------------------------------


def _resolve_dotted(node: ast.AST) -> str | None:
    """Render a Name/Attribute chain as a dotted string, or None for dynamic forms.

    ``foo`` → ``"foo"``.
    ``a.b.c`` → ``"a.b.c"``.
    ``self.x`` → ``"self.x"``.
    Anything containing a ``Call``, ``Subscript``, comprehension, etc. → None.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _resolve_dotted(node.value)
        if prefix is None:
            return None
        return f"{prefix}.{node.attr}"
    return None


def _alias_targets(expr: ast.AST) -> list[str]:
    """Possible dotted targets of an assignment RHS.

    Handles the two real forms in shipped plugins:

      - Plain reference: ``subprocess.run`` → ``["subprocess.run"]``.
      - ``or`` chain: ``run_fn or subprocess.run`` → both branches recursed,
        first known-target wins at lookup time.

    Returns the candidate list in source order; lookup time picks the first
    one that resolves to a known capability target. Non-dotted forms (calls,
    subscripts, lambdas) collapse to ``[]``.
    """
    if isinstance(expr, (ast.Name, ast.Attribute)):
        d = _resolve_dotted(expr)
        return [d] if d else []
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
        out: list[str] = []
        for v in expr.values:
            out.extend(_alias_targets(v))
        return out
    return []


def _is_known_target(target: str) -> bool:
    if target in DOTTED_TARGETS:
        return True
    last = target.rsplit(".", 1)[-1]
    if last in METHOD_NAMES or last in _BARE_NAMES:
        return True
    return False


class _AliasCollector(ast.NodeVisitor):
    """First pass: collect alias bindings the call walker resolves through.

    Module-level: ``alias = subprocess.run`` (top-level Assign with a Name
    target). Recorded in ``module_aliases``.

    Instance: ``self.x = ... or subprocess.run`` inside any class's
    ``__init__``. Recorded in ``self_aliases`` keyed ``"self.x"`` (the
    dotted form the call walker will look up).

    Only bindings whose RHS resolves to a known capability target are kept —
    incidental aliases (e.g. ``self._fetch = fetch_fn or _default_fetch``)
    are dropped because ``_default_fetch`` isn't a known target. The walker
    will still pick up calls inside ``_default_fetch`` itself when it walks
    that function body.
    """

    def __init__(self) -> None:
        self.module_aliases: dict[str, str] = {}
        self.self_aliases: dict[str, str] = {}

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                self._collect_module_assign(stmt)
            elif isinstance(stmt, ast.ClassDef):
                self._visit_class(stmt)
        # No further recursion at this stage — call walker handles the rest.

    def _collect_module_assign(self, stmt: ast.Assign) -> None:
        for target in stmt.targets:
            if not isinstance(target, ast.Name):
                continue
            for cand in _alias_targets(stmt.value):
                if _is_known_target(cand):
                    self.module_aliases[target.id] = cand
                    break

    def _visit_class(self, cls: ast.ClassDef) -> None:
        for item in cls.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name != "__init__":
                continue
            for stmt in ast.walk(item):
                if isinstance(stmt, ast.Assign):
                    self._collect_init_assign(stmt)

    def _collect_init_assign(self, stmt: ast.Assign) -> None:
        for target in stmt.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                continue
            key = f"self.{target.attr}"
            for cand in _alias_targets(stmt.value):
                if _is_known_target(cand):
                    self.self_aliases[key] = cand
                    break


class _CallVisitor(ast.NodeVisitor):
    """Second pass: walk every Call in the module and record under-declarations.

    ``generic_visit`` is the default — that recurses into every body
    including nested functions, lambdas, decorators, default-arg
    expressions, and comprehensions, matching the issue's "whole source
    file" coverage requirement.
    """

    def __init__(
        self,
        *,
        module_aliases: dict[str, str],
        self_aliases: dict[str, str],
        declared: frozenset[str],
        source_lines: list[str],
    ) -> None:
        self._module_aliases = module_aliases
        self._self_aliases = self_aliases
        self._declared = declared
        self._source_lines = source_lines
        self.findings: list[Finding] = []

    def visit_Call(self, node: ast.Call) -> None:
        self._inspect(node)
        self.generic_visit(node)

    def _inspect(self, node: ast.Call) -> None:
        resolved, requirement = self._classify(node.func)
        if requirement is None:
            return
        # The plugin satisfies an any-of requirement by declaring at least
        # one of the listed classes.
        if any(cap.value in self._declared for cap in requirement):
            return
        self.findings.append(
            Finding(
                line=node.lineno,
                col=node.col_offset,
                snippet=self._snippet(node),
                target=resolved,
                required=requirement,
                declared=self._declared,
            )
        )

    def _classify(
        self, func_node: ast.AST,
    ) -> tuple[str, frozenset[Capability] | None]:
        """Return (resolved-target-string, requirement-set-or-None).

        The resolved string is always non-empty for reporting purposes — it
        falls back to "<dynamic>" or a bare-attr label when the receiver
        can't be dot-resolved. The requirement is None when nothing in the
        tables applies.
        """
        dotted = _resolve_dotted(func_node)

        # ----- Path-style method-name fallback for non-dotted receivers -----
        #
        # ``Path('x').read_text()`` and ``some_expr.write_text()`` both have
        # an Attribute func whose receiver isn't dot-resolvable (the receiver
        # is a Call / Subscript / etc.). Static type inference is out of v1
        # scope, but the method name is unambiguous enough to classify:
        # ``read_text`` is read, ``write_text`` is write, ``unlink`` is
        # delete. METHOD_NAMES holds these.
        if dotted is None:
            if isinstance(func_node, ast.Attribute):
                method = METHOD_NAMES.get(func_node.attr)
                if method is not None:
                    return f"<receiver>.{func_node.attr}", method
            # Dynamic dispatch: getattr(...), subscript with no known method.
            # Per issue body and #46's static scan, classify as "unknown" —
            # does not fail completeness.
            return "<dynamic>", None

        # Alias resolution — module aliases first (single-name bindings),
        # then self-attr aliases (`self.x`).
        if dotted in self._module_aliases:
            dotted = self._module_aliases[dotted]
        elif dotted in self._self_aliases:
            dotted = self._self_aliases[dotted]

        # Dotted table hit.
        req = DOTTED_TARGETS.get(dotted)
        if req is not None:
            return dotted, req

        # Bare-name table (`open`, ...).
        if "." not in dotted:
            bare = _BARE_NAMES.get(dotted)
            if bare is not None:
                return dotted, bare

        # Receiver-typed method fallback: last dotted segment.
        last = dotted.rsplit(".", 1)[-1]
        method = METHOD_NAMES.get(last)
        if method is not None:
            return dotted, method

        return dotted, None

    def _snippet(self, node: ast.Call) -> str:
        """Produce a one-line snippet for the failure message.

        Prefer ``ast.unparse`` (3.9+) but cap length so a multi-line call
        renders as a single bounded line. Fall back to the source line.
        """
        try:
            text = ast.unparse(node)
        except Exception:
            line_idx = node.lineno - 1
            text = self._source_lines[line_idx].strip() if 0 <= line_idx < len(self._source_lines) else "<call>"
        text = text.replace("\n", " ")
        if len(text) > 80:
            text = text[:77] + "..."
        return text


__all__ = [
    "REASON_UNDER_DECLARED",
    "DOTTED_TARGETS",
    "METHOD_NAMES",
    "Finding",
    "CompletenessError",
    "check_completeness",
    "assert_complete",
    "format_findings",
]
