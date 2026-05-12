"""
Plugin builder — Issue #30. Felix's growth loop in code.

Tools: builder_create, builder_list_generated, builder_smoke_test.

Given a natural-language description (and an optional name), the builder asks
the LLM to emit a complete Python MCP plugin. It then:
  1. Validates the requested plugin name (lowercase identifier, no traversal).
  2. Refuses if a plugin with that name already exists (flat or subdir form).
  3. Validates the generated source: must define `PLUGIN_NAME` + `create()`,
     and must not contain known-dangerous patterns (`os.system`, `exec`,
     `eval`, …). Static AST/text scan only — sandboxing is out of scope.
  4. Installs each declared pip dependency, but only if it matches a vetted
     allowlist (default empty — refuses any unless caller supplies one).
  5. Writes server.py + README.md to a TEMP staging dir, loads it in-process,
     calls the smoke tool through the in-memory plugin instance.
  6. On smoke pass: moves the staged dir to plugins/<name>/, registers the
     live instance with the orchestrator, records the name in the
     "generated" set so it shows up in builder_list_generated.
  7. On any failure: reports the error, leaves no files behind, does not
     register anything.

Trust model: the smoke runner imports the generated module in this process,
so the generated code runs as Felix. The text-scan guardrail is a backstop,
not a sandbox — the model is the primary trust boundary. All side effects
(`llm_fn`, `pip_install_fn`, `smoke_runner_fn`) are injected so this whole
flow runs hermetically in tests.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from cerebral.mcp.orchestrator import MCPOrchestrator, Plugin, Tool, ToolResult
from cerebral.security import CAPABILITY_VOCABULARY

logger = logging.getLogger(__name__)

PLUGIN_NAME = "builder"

# ADR-0005 / Issue #44 — builder_create installs new plugin source on disk
# and may pip-install third-party packages. Both are code_install.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"code_install"})

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DEP_NAME_RE = re.compile(r"^([A-Za-z0-9_.\-]+)")  # captures package name from `name==1.2.3`

_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bos\.system\s*\(", "os.system"),
    (r"\bsubprocess\.(?:Popen|run|call|check_output|check_call)\s*\(", "subprocess shell-out"),
    (r"\bos\.popen\s*\(", "os.popen"),
    (r"^\s*from\s+os\s+import\s+system", "from os import system"),
    (r"\b__import__\s*\(\s*['\"]os['\"]\s*\)", "__import__('os')"),
    (r"(?<!\.)\bexec\s*\(", "exec()"),
    (r"(?<!\.)\beval\s*\(", "eval()"),
    (r"\bopen\s*\([^)]*['\"]w['\"]", "raw file write"),
)


LlmFn = Callable[..., dict]
PipInstallFn = Callable[[str], None]
SmokeRunnerFn = Callable[..., Awaitable[ToolResult]]


def _default_llm(description: str, suggested_name: str | None = None) -> dict:
    """Default LLM hook — model router will replace this in main.py."""
    raise NotImplementedError(
        "BuilderPlugin requires an llm_fn — main.py must wire the model router in."
    )


def _default_pip_install(dep: str) -> None:
    cmd = [sys.executable, "-m", "pip", "install", dep]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pip install {dep} failed: {proc.stderr.strip()}")


async def _default_smoke_runner(plugin: Plugin, tool_name: str, args: dict) -> ToolResult:
    """In-process smoke: call the tool and assert no exception / non-error result."""
    result = await plugin.call_tool(tool_name, args)
    if result.is_error:
        raise RuntimeError(f"smoke tool {tool_name!r} returned error: {result.content}")
    return result


class BuilderPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        *,
        orchestrator: MCPOrchestrator,
        plugins_dir: Path,
        llm_fn: LlmFn | None = None,
        pip_install_fn: PipInstallFn | None = None,
        smoke_runner_fn: SmokeRunnerFn | None = None,
        pip_allowlist: Iterable[str] = (),
    ) -> None:
        self._orc = orchestrator
        self._plugins_dir = Path(plugins_dir)
        self._llm = llm_fn or _default_llm
        self._pip_install = pip_install_fn or _default_pip_install
        self._smoke = smoke_runner_fn or _default_smoke_runner
        self._pip_allowlist = {dep.strip().lower() for dep in pip_allowlist}
        # Plugin names produced by *this* builder (process-local).
        self._generated: set[str] = set()

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="builder_create",
                description=(
                    "Generate a new MCP plugin from a natural-language description, "
                    "smoke-test it, and register it with the running orchestrator. "
                    "Use this when the user says 'I need you to be able to X' and no "
                    "existing tool covers X. The LLM payload must include "
                    "'required_capabilities' (list of class names from the 16-class "
                    "vocabulary) — see ADR-0005."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Plain-language capability the user wants.",
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Optional snake_case plugin name. The LLM picks one if omitted."
                            ),
                        },
                    },
                    "required": ["description"],
                },
            ),
            Tool(
                name="builder_list_generated",
                description="List plugins created by builder_create in this Cerebral process.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="builder_smoke_test",
                description=(
                    "Re-run the smoke test for a previously-generated plugin "
                    "(useful after a runtime error, before re-registering it)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Plugin name."},
                        "tool_name": {
                            "type": "string",
                            "description": "Tool to invoke. Defaults to the first listed tool.",
                        },
                        "args": {
                            "type": "object",
                            "description": "Args for the smoke tool. Default: {}.",
                        },
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "builder_create":
            return await self._create(args)
        if tool_name == "builder_list_generated":
            return self._list_generated()
        if tool_name == "builder_smoke_test":
            return await self._smoke_test(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # builder_create
    # ------------------------------------------------------------------

    async def _create(self, args: dict) -> ToolResult:
        description = (args or {}).get("description", "").strip()
        suggested = (args or {}).get("name")
        if not description:
            return ToolResult(content="description is required", is_error=True)

        try:
            payload = self._llm(description, suggested)
        except Exception as exc:
            return ToolResult(content=f"LLM call failed: {exc}", is_error=True)

        name = (payload.get("name") or "").strip()
        if not _NAME_RE.match(name):
            return ToolResult(
                content=(
                    f"Invalid plugin name {name!r}: must match [a-z][a-z0-9_]* "
                    "(lowercase, no dashes, no path separators)."
                ),
                is_error=True,
            )

        target_dir = self._plugins_dir / name
        flat_path = self._plugins_dir / f"{name}.py"
        if target_dir.exists() or flat_path.exists():
            return ToolResult(
                content=f"Plugin {name!r} already exists in {self._plugins_dir}",
                is_error=True,
            )

        server_py = payload.get("server_py", "")
        readme_md = payload.get("readme_md", f"# {name}\n\nGenerated by Felix.\n")
        pip_deps: list[str] = list(payload.get("pip_deps") or [])
        smoke_tool = payload.get("smoke_tool")
        smoke_args = payload.get("smoke_args") or {}

        # ----- ADR-0005 / Issue #44 — capability declaration -----
        required_capabilities_raw = payload.get("required_capabilities")
        if required_capabilities_raw is None:
            return ToolResult(
                content=(
                    "LLM payload missing 'required_capabilities' — every "
                    "generated plugin must declare its minimum capability "
                    "classes (ADR-0005)."
                ),
                is_error=True,
            )
        try:
            required_capabilities = frozenset(str(c) for c in required_capabilities_raw)
        except TypeError:
            return ToolResult(
                content="'required_capabilities' must be an iterable of strings",
                is_error=True,
            )
        unknown_caps = required_capabilities - CAPABILITY_VOCABULARY
        if unknown_caps:
            return ToolResult(
                content=(
                    "Generated plugin declares unknown capability classes: "
                    f"{sorted(unknown_caps)}. Allowed: "
                    f"{sorted(CAPABILITY_VOCABULARY)}"
                ),
                is_error=True,
            )

        # The orchestrator reads REQUIRED_CAPABILITIES at module load. If the
        # LLM omitted the constant, inject it from the validated payload so
        # the generated plugin survives a Cerebral restart.
        server_py = self._ensure_required_capabilities_constant(
            server_py, required_capabilities,
        )

        # ----- Static guardrails on generated code -----
        ok, reason = self._scan_generated_code(server_py)
        if not ok:
            return ToolResult(content=f"Generated code rejected: {reason}", is_error=True)

        # ----- pip allowlist + install -----
        for dep in pip_deps:
            pkg = self._dep_root_name(dep)
            if pkg.lower() not in self._pip_allowlist:
                return ToolResult(
                    content=(
                        f"Dependency {dep!r} not permitted by pip_allowlist "
                        f"({sorted(self._pip_allowlist) or 'empty'}). "
                        "Add it to the allowlist if you trust it."
                    ),
                    is_error=True,
                )

        for dep in pip_deps:
            try:
                self._pip_install(dep)
            except Exception as exc:
                return ToolResult(
                    content=f"pip install failed for {dep!r}: {exc}",
                    is_error=True,
                )

        # ----- Stage to a temp dir, smoke-test, then move -----
        with tempfile.TemporaryDirectory(prefix="builder_") as staging_str:
            staging = Path(staging_str)
            stage_dir = staging / name
            stage_dir.mkdir()
            (stage_dir / "server.py").write_text(server_py, encoding="utf-8")
            (stage_dir / "README.md").write_text(readme_md, encoding="utf-8")

            try:
                plugin = self._import_module(stage_dir / "server.py", name)
            except Exception as exc:
                return ToolResult(
                    content=f"Could not import generated plugin: {exc}",
                    is_error=True,
                )

            tools = plugin.list_tools()
            chosen_tool = smoke_tool or (tools[0].name if tools else None)
            if not chosen_tool:
                return ToolResult(
                    content="Generated plugin exposes no tools — nothing to smoke-test.",
                    is_error=True,
                )

            try:
                await self._smoke(plugin, chosen_tool, smoke_args)
            except Exception as exc:
                return ToolResult(
                    content=f"Smoke test failed for {chosen_tool!r}: {exc}",
                    is_error=True,
                )

            # Smoke passed — move to plugins/<name>/ and register.
            self._plugins_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage_dir), str(target_dir))

        self._orc.register(plugin, required_capabilities=required_capabilities)
        self._generated.add(name)

        return ToolResult(
            content=json.dumps(
                {
                    "name": name,
                    "tool_count": len(tools),
                    "registered": True,
                    "path": str(target_dir),
                }
            )
        )

    # ------------------------------------------------------------------
    # builder_list_generated / builder_smoke_test
    # ------------------------------------------------------------------

    def _list_generated(self) -> ToolResult:
        return ToolResult(content=json.dumps({"generated": sorted(self._generated)}))

    async def _smoke_test(self, args: dict) -> ToolResult:
        name = (args or {}).get("name", "").strip()
        if not _NAME_RE.match(name):
            return ToolResult(content=f"Invalid plugin name {name!r}", is_error=True)
        path = self._plugins_dir / name / "server.py"
        if not path.is_file():
            return ToolResult(
                content=f"No generated plugin at {path}",
                is_error=True,
            )
        try:
            plugin = self._import_module(path, name)
        except Exception as exc:
            return ToolResult(content=f"Import failed: {exc}", is_error=True)

        tool_name = args.get("tool_name") or (
            plugin.list_tools()[0].name if plugin.list_tools() else None
        )
        if not tool_name:
            return ToolResult(content="Plugin has no tools to smoke-test.", is_error=True)
        try:
            result = await self._smoke(plugin, tool_name, args.get("args") or {})
        except Exception as exc:
            return ToolResult(content=f"Smoke failed: {exc}", is_error=True)
        return ToolResult(
            content=json.dumps(
                {"name": name, "tool": tool_name, "result": result.content}
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dep_root_name(dep: str) -> str:
        """Strip version pin / extras: 'requests==2.31.0' → 'requests'."""
        m = _DEP_NAME_RE.match(dep.strip())
        return m.group(1) if m else dep.strip()

    @staticmethod
    def _scan_generated_code(source: str) -> tuple[bool, str]:
        if "PLUGIN_NAME" not in source:
            return False, "missing PLUGIN_NAME constant"
        if "REQUIRED_CAPABILITIES" not in source:
            return False, "missing REQUIRED_CAPABILITIES constant"
        if not re.search(r"^\s*def\s+create\s*\(", source, re.MULTILINE):
            return False, "missing create() factory"
        for pattern, label in _FORBIDDEN_PATTERNS:
            if re.search(pattern, source, re.MULTILINE):
                return False, f"forbidden / unsafe pattern: {label}"
        return True, ""

    @staticmethod
    def _ensure_required_capabilities_constant(
        source: str, required_capabilities: frozenset[str],
    ) -> str:
        """Inject REQUIRED_CAPABILITIES into generated source if absent.

        Builder-generated plugins must carry the module-level constant so
        they reload cleanly on Cerebral restart. The LLM may emit it
        already; if not, prepend a deterministic declaration so the source
        matches the validated payload exactly.
        """
        if "REQUIRED_CAPABILITIES" in source:
            return source
        literal = (
            "frozenset()"
            if not required_capabilities
            else "frozenset({" + ", ".join(
                repr(c) for c in sorted(required_capabilities)
            ) + "})"
        )
        injected = (
            "# Issue #44 — capability declaration injected by the builder.\n"
            f"REQUIRED_CAPABILITIES: frozenset[str] = {literal}\n\n"
        )
        return injected + source

    @staticmethod
    def _import_module(server_path: Path, plugin_name: str) -> Plugin:
        module_name = f"openmind_generated_{plugin_name}"
        spec = importlib.util.spec_from_file_location(module_name, server_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot create module spec for {server_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "create"):
            raise RuntimeError("generated module has no create()")
        return module.create()


# ---------------------------------------------------------------------------
# Plugin convention entry points
# ---------------------------------------------------------------------------


def create(orchestrator: MCPOrchestrator | None = None, plugins_dir: Path | None = None,
           **kwargs) -> BuilderPlugin:
    """
    Factory called by `MCPOrchestrator.discover_plugins`.

    The orchestrator does not currently pass itself in, so when discovered
    automatically we return a 'parked' instance with no orchestrator handle
    (its tools will refuse to run until `attach` is called by main.py).
    Tests construct BuilderPlugin directly and bypass this.
    """
    if orchestrator is None:
        # Lazy parked instance — main.py calls attach() once the orchestrator exists.
        return _ParkedBuilderPlugin(plugins_dir=plugins_dir or Path("plugins"))
    return BuilderPlugin(
        orchestrator=orchestrator,
        plugins_dir=plugins_dir or Path("plugins"),
        **kwargs,
    )


class _ParkedBuilderPlugin(BuilderPlugin):
    """Stand-in returned during auto-discovery before main.py wires the orchestrator."""

    def __init__(self, plugins_dir: Path) -> None:
        # Skip super().__init__'s orchestrator requirement.
        self._orc = None  # type: ignore[assignment]
        self._plugins_dir = Path(plugins_dir)
        self._llm = _default_llm
        self._pip_install = _default_pip_install
        self._smoke = _default_smoke_runner
        self._pip_allowlist = set()
        self._generated = set()

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        return ToolResult(
            content=(
                "builder is not yet attached to a running orchestrator — "
                "main.py must call BuilderPlugin.attach() during startup."
            ),
            is_error=True,
        )

    def attach(self, orchestrator: MCPOrchestrator, *,
               llm_fn: LlmFn | None = None,
               pip_install_fn: PipInstallFn | None = None,
               smoke_runner_fn: SmokeRunnerFn | None = None,
               pip_allowlist: Iterable[str] = ()) -> None:
        self._orc = orchestrator
        if llm_fn is not None:
            self._llm = llm_fn
        if pip_install_fn is not None:
            self._pip_install = pip_install_fn
        if smoke_runner_fn is not None:
            self._smoke = smoke_runner_fn
        self._pip_allowlist = {dep.strip().lower() for dep in pip_allowlist}
        # Restore real call_tool (drop our parked override):
        self.call_tool = BuilderPlugin.call_tool.__get__(self, BuilderPlugin)  # type: ignore[method-assign]
