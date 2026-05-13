"""
MCP Orchestrator — Issue #7.

Manages the registry of MCP plugin servers, routes tool calls, and exposes a
unified tool list to the model router.

Public interface:
  orc = MCPOrchestrator()
  orc.register(plugin)                        # add a plugin
  orc.unregister("clock")                     # remove by name
  orc.discover_plugins(Path("../plugins"))    # auto-load from directory
  orc.list_tools()                            # → list[Tool]
  await orc.call_tool("get_time", {})         # → ToolResult (never raises)
  orc.tools_for_llm                           # → list[dict] for LLM tool use
  orc.registration_errors                     # plugins refused at load time

Plugin convention (plugins/*.py):
  PLUGIN_NAME: str = "clock"
  REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read"})  # Issue #44
  def create() -> Plugin: ...
"""

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from cerebral.security import (
    CAPABILITY_VOCABULARY,
    Capability,
    CallFlags,
    CapabilityGate,
    Decision,
    INSPECTED,
    REASON_FORBIDDEN_PATTERN,
    REASON_NON_TEXT,
    REASON_NOT_INSPECTABLE_PATH,
    ProfileACL,
    TRUSTED,
    scan_source,
)

logger = logging.getLogger(__name__)


# Sentinel for "attribute missing" — distinguishes from a plugin that
# legitimately set REQUIRED_CAPABILITIES = frozenset() (no capabilities used).
_MISSING = object()


# Refusal reason codes — stable strings consumed by the tray's plugin list.
# REASON_FORBIDDEN_PATTERN / REASON_NON_TEXT / REASON_NOT_INSPECTABLE_PATH
# live in cerebral.security.inspectability and are re-exported here as part
# of the public refusal-reason vocabulary (Issue #46).
REASON_MISSING = "missing_required_capabilities"
REASON_INVALID_TYPE = "invalid_required_capabilities"
REASON_UNKNOWN_CAPABILITY = "unknown_capability"
REASON_CREATE_FAILED = "create_failed"
REASON_LOAD_FAILED = "load_failed"


class PluginRegistrationError(Exception):
    """A plugin was refused at registration time (ADR-0005, Issue #44).

    Carries a structured `reason` code so the tray can render a stable
    message regardless of the underlying detail string.
    """

    def __init__(self, plugin_name: str, reason: str, detail: str = "") -> None:
        self.plugin_name = plugin_name
        self.reason = reason
        self.detail = detail
        suffix = f" — {detail}" if detail else ""
        super().__init__(f"Refused plugin {plugin_name!r}: {reason}{suffix}")


@dataclass
class Tool:
    name: str
    description: str
    plugin: str
    schema: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@runtime_checkable
class Plugin(Protocol):
    name: str

    def list_tools(self) -> list[Tool]: ...
    async def call_tool(self, tool_name: str, args: dict) -> ToolResult: ...


def _validate_required_capabilities(
    plugin_name: str, required: object
) -> PluginRegistrationError | None:
    """Validate a REQUIRED_CAPABILITIES declaration against the closed vocab.

    Returns None when valid, or a PluginRegistrationError describing the gap.
    The error is *returned* (not raised) so callers can record it without a
    try/except dance.
    """
    if required is _MISSING:
        return PluginRegistrationError(
            plugin_name,
            REASON_MISSING,
            "module has no REQUIRED_CAPABILITIES declaration",
        )
    if not isinstance(required, frozenset):
        return PluginRegistrationError(
            plugin_name,
            REASON_INVALID_TYPE,
            f"REQUIRED_CAPABILITIES must be a frozenset[str], got {type(required).__name__}",
        )
    if not all(isinstance(item, str) for item in required):
        return PluginRegistrationError(
            plugin_name,
            REASON_INVALID_TYPE,
            "REQUIRED_CAPABILITIES must contain only str values",
        )
    unknown = set(required) - CAPABILITY_VOCABULARY
    if unknown:
        return PluginRegistrationError(
            plugin_name,
            REASON_UNKNOWN_CAPABILITY,
            f"not in 16-class vocabulary: {sorted(unknown)}",
        )
    return None


class MCPOrchestrator:
    def __init__(
        self,
        gate: CapabilityGate | None = None,
        *,
        acl: ProfileACL | None = None,
    ) -> None:
        self._plugins: dict[str, Plugin] = {}
        # tool_name → plugin_name for fast routing
        self._tool_index: dict[str, str] = {}
        # The capability gate enforces ADR-0005's day-1 policy. The optional
        # ACL resolver (Issue #45) layers per-profile overrides + RAM-only
        # once/session grants on top; when present, the gate's lookup is
        # only consulted indirectly via the ACL's snapshot fallback.
        self._gate: CapabilityGate = gate or CapabilityGate()
        self._acl: ProfileACL | None = acl
        # Module-level REQUIRED_CAPABILITIES per registered plugin (Issue #44).
        # Used by the tray UI and (later) by #47 to decide per-tool gates.
        self._plugin_capabilities: dict[str, frozenset[str]] = {}
        # Per-plugin inspectability mark — "inspected" or "trusted" (Issue #46).
        # Set during discover_plugins; the tray uses this to render the red
        # "trusted, unverified" badge on plugins/_trusted/<name>/server.py.
        # Plugins registered directly via register() (tests, parked builder)
        # default to "inspected" — they bypass on-disk discovery entirely.
        self._plugin_inspectability: dict[str, str] = {}
        # Plugins refused at load time, ordered by discovery order.
        # Each entry is {plugin_name, reason, detail, path}. Surfaced to the
        # tray so the user sees *why* a plugin didn't load.
        self._registration_errors: list[dict] = []

    def set_acl(self, acl: ProfileACL | None) -> None:
        """Swap the active profile's ACL resolver.

        Called by main.py on profile switch (Issue #45). Passing None falls
        back to the bare capability gate (useful for tests and for the
        no-profile bootstrap state).
        """
        self._acl = acl

    @property
    def acl(self) -> ProfileACL | None:
        return self._acl

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register(
        self,
        plugin: Plugin,
        *,
        required_capabilities: frozenset[str] | None = None,
    ) -> None:
        """Register a plugin.

        When `required_capabilities` is provided, the orchestrator validates
        it against the closed 16-class vocabulary (ADR-0005, Issue #44) and
        raises ``PluginRegistrationError`` on mismatch. ``discover_plugins``
        always passes the module's declaration; direct callers (the builder,
        tests) may omit it for backward compatibility.
        """
        if required_capabilities is not None:
            err = _validate_required_capabilities(plugin.name, required_capabilities)
            if err is not None:
                raise err
            self._plugin_capabilities[plugin.name] = required_capabilities
        if plugin.name in self._plugins:
            logger.warning("[mcp] Plugin '%s' already registered — replacing", plugin.name)
            self._remove_from_index(plugin.name)
        self._plugins[plugin.name] = plugin
        for tool in plugin.list_tools():
            if tool.name in self._tool_index:
                existing = self._tool_index[tool.name]
                logger.warning(
                    "[mcp] Tool '%s' already registered by '%s'; '%s' takes over",
                    tool.name, existing, plugin.name,
                )
            self._tool_index[tool.name] = plugin.name
        logger.info("[mcp] Registered plugin '%s' with %d tool(s)", plugin.name, len(plugin.list_tools()))

    def unregister(self, plugin_name: str) -> None:
        if plugin_name not in self._plugins:
            logger.warning("[mcp] Tried to unregister unknown plugin '%s'", plugin_name)
            return
        self._remove_from_index(plugin_name)
        del self._plugins[plugin_name]
        self._plugin_capabilities.pop(plugin_name, None)
        self._plugin_inspectability.pop(plugin_name, None)
        logger.info("[mcp] Unregistered plugin '%s'", plugin_name)

    # ------------------------------------------------------------------
    # Registration-error surface (Issue #44 — tray plugin list)
    # ------------------------------------------------------------------

    @property
    def registration_errors(self) -> list[dict]:
        """Plugins refused at load time, oldest first.

        Each entry: ``{plugin_name, reason, detail, path}`` where ``reason``
        is one of the ``REASON_*`` constants. Stable shape — the tray's
        plugin-list renderer reads this verbatim.
        """
        return list(self._registration_errors)

    def required_capabilities_for(self, plugin_name: str) -> frozenset[str] | None:
        """The REQUIRED_CAPABILITIES set the named plugin declared at load
        time, or None if it was registered without one (legacy register()
        path; tests)."""
        return self._plugin_capabilities.get(plugin_name)

    def inspectability_for(self, plugin_name: str) -> str | None:
        """The inspectability mark recorded at discovery time (Issue #46).

        Returns ``"inspected"`` for the default text-Python form, ``"trusted"``
        for plugins loaded from ``plugins/_trusted/``, or ``None`` for plugins
        registered directly via ``register()`` (tests / the parked builder
        instance) — those bypass on-disk discovery and therefore have no
        inspectability mark.
        """
        return self._plugin_inspectability.get(plugin_name)

    def _remove_from_index(self, plugin_name: str) -> None:
        to_remove = [k for k, v in self._tool_index.items() if v == plugin_name]
        for key in to_remove:
            del self._tool_index[key]

    # ------------------------------------------------------------------
    # Tool access
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for plugin in self._plugins.values():
            tools.extend(plugin.list_tools())
        return tools

    async def call_tool(
        self,
        name: str,
        args: dict,
        capability: Capability | None = None,
        flags: CallFlags | None = None,
    ) -> ToolResult:
        if name not in self._tool_index:
            logger.warning("[mcp] Unknown tool '%s'", name)
            return ToolResult(content=f"Unknown tool: '{name}'", is_error=True)
        if capability is not None:
            # Issue #45 — the per-profile ACL resolver (when set) layers
            # per-tool overrides + once/session grants on top of the gate's
            # default-policy lookup. Without an ACL we fall back to the
            # gate directly; the resolved decision is the same shape either
            # way (SILENT / ASK / DENY).
            if self._acl is not None:
                decision = self._acl.resolve(capability, name, flags)
            else:
                decision = self._gate.check(capability, flags)
            # ASK resolves to DENY in this slice (#43/#45, fail-closed).
            # The consent surface that lets ASK reach the user is #48.
            if decision is not Decision.SILENT:
                logger.info(
                    "[mcp] Gate denied '%s' (capability=%s, decision=%s)",
                    name, capability.value, decision.value,
                )
                return ToolResult(
                    content=(
                        f"Denied: '{name}' requires capability "
                        f"'{capability.value}' (policy: {decision.value})"
                    ),
                    is_error=True,
                )
        plugin_name = self._tool_index[name]
        plugin = self._plugins[plugin_name]
        try:
            result = await plugin.call_tool(name, args)
        except Exception as exc:
            logger.error("[mcp] Tool '%s' raised: %s", name, exc)
            return ToolResult(content=f"Tool '{name}' error: {exc}", is_error=True)
        return result

    @property
    def tools_for_llm(self) -> list[dict]:
        """Format tools as the LLM tool-use schema (Anthropic/OpenAI compatible)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.schema or {"type": "object", "properties": {}},
            }
            for t in self.list_tools()
        ]

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    def discover_plugins(self, plugins_dir: Path) -> None:
        """Import every *.py in plugins_dir, call create(), register the result.

        Conforming layouts (ADR-0005, Issue #46):
          - plugins/<name>.py            (flat, original convention)
          - plugins/<name>/server.py     (subdir, used by the builder for #30)
          - plugins/_trusted/<name>/server.py  (escape hatch — scan skipped,
                                                tray flags as "trusted, unverified")

        Subdirs that don't match any of these (e.g. a folder with no server.py)
        are recorded as ``REASON_NOT_INSPECTABLE_PATH`` so the tray can flag
        the layout error rather than silently hiding the plugin.
        """
        if not plugins_dir.is_dir():
            logger.warning("[mcp] plugins_dir '%s' does not exist — skipping discovery", plugins_dir)
            return
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self._load_plugin_file(path, inspectability=INSPECTED)
        for sub in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
            if sub.name.startswith("."):
                continue
            if sub.name == "__pycache__":
                continue
            if sub.name == "_trusted":
                self._discover_trusted_subtree(sub)
                continue
            if sub.name.startswith("_"):
                # Other underscored dirs are reserved scaffolding; ignore.
                continue
            server_py = sub / "server.py"
            if server_py.is_file():
                self._load_plugin_file(server_py, inspectability=INSPECTED)
            else:
                self._record_registration_error(
                    sub.name,
                    REASON_NOT_INSPECTABLE_PATH,
                    (
                        f"plugin folder {sub.name!r} has no server.py — expected "
                        "plugins/<name>/server.py"
                    ),
                    sub,
                )

    def _discover_trusted_subtree(self, trusted_dir: Path) -> None:
        """Walk ``plugins/_trusted/`` (Issue #46 escape hatch).

        Trusted plugins skip the static-pattern scan but still:
          - must live at ``plugins/_trusted/<name>/server.py``
          - must declare ``REQUIRED_CAPABILITIES``
          - are gated at call time exactly like inspected plugins
          - render with a permanent red "trusted, unverified" badge in the tray
        """
        for sub in sorted(p for p in trusted_dir.iterdir() if p.is_dir()):
            if sub.name.startswith(".") or sub.name.startswith("_"):
                continue
            server_py = sub / "server.py"
            if server_py.is_file():
                self._load_plugin_file(server_py, inspectability=TRUSTED)
            else:
                self._record_registration_error(
                    sub.name,
                    REASON_NOT_INSPECTABLE_PATH,
                    (
                        f"trusted plugin folder {sub.name!r} has no server.py — "
                        "expected plugins/_trusted/<name>/server.py"
                    ),
                    sub,
                )

    def _load_plugin_file(self, path: Path, *, inspectability: str = INSPECTED) -> None:
        # ADR-0005 / Issue #46 — static-pattern scan runs BEFORE module import
        # so a hostile plugin's import-time side effects never fire on a
        # refused plugin. Trusted plugins skip the scan but still go through
        # REQUIRED_CAPABILITIES validation and the runtime capability gate.
        if inspectability == INSPECTED:
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self._record_registration_error(
                    path.stem, REASON_NON_TEXT,
                    f"plugin source is not valid UTF-8 text: {exc}", path,
                )
                return
            except OSError as exc:
                self._record_registration_error(
                    path.stem, REASON_LOAD_FAILED,
                    f"could not read plugin source: {exc}", path,
                )
                return
            issue = scan_source(source)
            if issue is not None:
                self._record_registration_error(
                    path.stem, issue.reason, issue.detail, path,
                )
                return

        module_name = f"openmind_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            self._record_registration_error(
                path.stem, REASON_LOAD_FAILED,
                f"could not create import spec for {path}", path,
            )
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            self._record_registration_error(
                path.stem, REASON_LOAD_FAILED, f"module exec failed: {exc}", path,
            )
            return
        if not hasattr(module, "create"):
            self._record_registration_error(
                path.stem, REASON_LOAD_FAILED,
                "module has no create() factory",
                path,
            )
            return

        # ADR-0005 / Issue #44: validate the plugin's REQUIRED_CAPABILITIES
        # against the closed 16-class vocabulary BEFORE calling create().
        # Refused plugins surface to the tray via registration_errors and
        # never produce side effects from their factory.
        plugin_name = getattr(module, "PLUGIN_NAME", path.stem)
        required = getattr(module, "REQUIRED_CAPABILITIES", _MISSING)
        err = _validate_required_capabilities(plugin_name, required)
        if err is not None:
            self._record_registration_error(err.plugin_name, err.reason, err.detail, path)
            return

        try:
            plugin = module.create()
        except Exception as exc:
            self._record_registration_error(
                plugin_name, REASON_CREATE_FAILED, f"create() raised: {exc}", path,
            )
            return
        try:
            self.register(plugin, required_capabilities=required)
        except PluginRegistrationError as exc:
            # Belt-and-suspenders — _validate_required_capabilities already
            # passed, so register() should not raise. Record anyway.
            self._record_registration_error(exc.plugin_name, exc.reason, exc.detail, path)
            return
        self._plugin_inspectability[plugin.name] = inspectability

    def _record_registration_error(
        self, plugin_name: str, reason: str, detail: str, path: Path,
    ) -> None:
        entry = {
            "plugin_name": plugin_name,
            "reason": reason,
            "detail": detail,
            "path": str(path),
        }
        self._registration_errors.append(entry)
        logger.warning(
            "[mcp] Refused plugin %r at %s — %s: %s",
            plugin_name, path, reason, detail,
        )
