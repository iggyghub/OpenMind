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
from types import ModuleType
from typing import Protocol, runtime_checkable

from cerebral.security import (
    CAPABILITY_VOCABULARY,
    Capability,
    CallFlags,
    CapabilityGate,
    ConsentSurface,
    Decision,
    INSPECTED,
    ModalSurface,
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


# Decision strictness order for the AND-across-capabilities check (#52).
# A higher rank means "more restrictive" — DENY trumps ASK trumps SILENT.
_DECISION_RANK: dict[Decision, int] = {
    Decision.SILENT: 0,
    Decision.ASK: 1,
    Decision.DENY: 2,
}


def _worse(a: Decision, b: Decision) -> bool:
    """True iff ``a`` is strictly more restrictive than ``b``."""
    return _DECISION_RANK[a] > _DECISION_RANK[b]


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
    # Per-tool irreversible declaration (Issue #139, ADR-0005 amendment
    # 2026-05-20). Defaults to False; plugins opt in for write-class tools
    # whose effect cannot be undone (e.g. ``gmail_send``). The orchestrator
    # ORs this into ``CallFlags`` at dispatch so the call routes through the
    # ModalSurface even when a Session/Persistent grant would otherwise
    # cover the class. The 16-class capability vocabulary is unchanged.
    irreversible: bool = False
    required_capabilities: frozenset[str] | None = None


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
        consent: ConsentSurface | None = None,
        modal: ModalSurface | None = None,
    ) -> None:
        self._plugins: dict[str, Plugin] = {}
        # tool_name → plugin_name for fast routing
        self._tool_index: dict[str, str] = {}
        # tool_name → Tool object for per-tool metadata lookup at dispatch
        # (Issue #139). Populated alongside ``_tool_index`` in ``register``
        # and cleared in ``_remove_from_index``. Today the only consumer is
        # the ``irreversible`` flag merge in ``call_tool`` /
        # ``check_capabilities``; future per-tool metadata reads share this
        # cache rather than re-walking ``plugin.list_tools()`` each call.
        self._tool_lookup: dict[str, Tool] = {}
        # tool_name → registration history as a list of (plugin_name, Tool)
        # entries in registration order. The LAST entry is the current owner
        # (mirrored into ``_tool_index`` / ``_tool_lookup``); earlier entries
        # are prior owners whose claim was superseded by a later registrant.
        # On unregister, the unregistering plugin's entries are filtered out
        # of each tool's history; the new last entry becomes the active
        # owner — so when (e.g.) ``google_workspace`` takes over ``gmail_send``
        # and is later unregistered, the original ``gmail`` plugin's claim is
        # restored instead of the tool silently disappearing from the index.
        self._tool_registrations: dict[str, list[tuple[str, Tool]]] = {}
        # The capability gate enforces ADR-0005's day-1 policy. The optional
        # ACL resolver (Issue #45) layers per-profile overrides + RAM-only
        # once/session grants on top; when present, the gate's lookup is
        # only consulted indirectly via the ACL's snapshot fallback.
        self._gate: CapabilityGate = gate or CapabilityGate()
        self._acl: ProfileACL | None = acl
        # Tray consent surface (Issue #48). When wired, an ASK decision
        # routes to the user via a tray notification with inline buttons.
        # When None, ASK continues to fail-closed to DENY (pre-#48 behaviour).
        self._consent: ConsentSurface | None = consent
        # Irreversible-flag modal surface (Issue #49). When wired, a call
        # with ``flags.irreversible=True`` that wasn't already DENY'd by
        # the gate/ACL routes through the modal — even if a Session or
        # Persistent grant would otherwise cover the class. When None,
        # irreversible fails closed to DENY regardless of any grant.
        self._modal: ModalSurface | None = modal
        # Module-level REQUIRED_CAPABILITIES per registered plugin (Issue #44).
        # Used by the tray UI and (later) by #47 to decide per-tool gates.
        self._plugin_capabilities: dict[str, frozenset[str]] = {}
        # Per-plugin inspectability mark — "inspected" or "trusted" (Issue #46).
        # Set during discover_plugins; the tray uses this to render the red
        # "trusted, unverified" badge on plugins/_trusted/<name>/server.py.
        # Plugins registered directly via register() (tests, parked builder)
        # default to "inspected" — they bypass on-disk discovery entirely.
        self._plugin_inspectability: dict[str, str] = {}
        # plugin_name → tool count at registration time, frozen after register().
        # Reflects how many tools the plugin declared, regardless of subsequent
        # takeovers by other plugins (e.g. google_workspace). The tray's
        # plugins_list event exposes this so superseded plugins (tool_count in
        # _tool_index → 0) still report a non-zero meaningful count.
        self._plugin_registration_tool_counts: dict[str, int] = {}
        # Plugins refused at load time, ordered by discovery order.
        # Each entry is {plugin_name, reason, detail, path}. Surfaced to the
        # tray so the user sees *why* a plugin didn't load.
        self._registration_errors: list[dict] = []
        # plugin_name → the actual loaded ModuleType from _load_plugin
        # (Issue #153). main.py's seam-injection calls
        # (set_token_provider, set_memory_factory) must target THIS module
        # instance — the one the orchestrator dispatches against — not the
        # separate instance Python would create for `import plugins.<name>`.
        # spec_from_file_location's `openmind_plugin_<stem>` module name was
        # chosen to avoid sys.modules conflicts with the rest of the import
        # graph; the trade-off is that the canonical name route produces a
        # second instance with its own module-level globals, which silently
        # broke every set_*_provider wiring until this seam was added.
        # Plugins registered directly via register() (tests, parked builder)
        # have no source file and are absent from this map by design.
        self._plugin_modules: dict[str, ModuleType] = {}
        # Harness UI S2 #470 — plugins that passed scan + capabilities check
        # but were NOT registered because the user disabled them. Keyed by
        # plugin name; each value is the metadata dict used by
        # _plugins_snapshot_data to render the card with status="disabled".
        self._disabled_plugins_meta: dict[str, dict] = {}

    def set_acl(self, acl: ProfileACL | None) -> None:
        """Swap the active profile's ACL resolver.

        Called by main.py on profile switch (Issue #45). Passing None falls
        back to the bare capability gate (useful for tests and for the
        no-profile bootstrap state).
        """
        self._acl = acl
        # The consent surface is profile-scoped (it mutates the ACL it
        # holds). Keep it in sync so it grants against the right profile.
        if self._consent is not None:
            self._consent.set_acl(acl)

    def set_consent_surface(self, consent: ConsentSurface | None) -> None:
        """Wire (or unwire) the tray consent surface (Issue #48).

        main.py wires this at startup after the WebSocket server is up.
        Tests inject a fake surface directly via the constructor.
        """
        self._consent = consent
        if consent is not None:
            consent.set_acl(self._acl)

    def set_modal_surface(self, modal: ModalSurface | None) -> None:
        """Wire (or unwire) the tray irreversible-modal surface (Issue #49).

        The modal carries no ACL — irreversible acceptance is one-shot,
        never persisted — so unlike ``set_consent_surface`` this is a
        flat assignment with no rebinding to do.
        """
        self._modal = modal

    @property
    def acl(self) -> ProfileACL | None:
        return self._acl

    @property
    def consent_surface(self) -> ConsentSurface | None:
        return self._consent

    @property
    def modal_surface(self) -> ModalSurface | None:
        return self._modal

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
        tools = plugin.list_tools()
        self._plugin_registration_tool_counts[plugin.name] = len(tools)
        for tool in tools:
            if tool.name in self._tool_index:
                existing = self._tool_index[tool.name]
                logger.warning(
                    "[mcp] Tool '%s' already registered by '%s'; '%s' takes over",
                    tool.name, existing, plugin.name,
                )
            self._tool_index[tool.name] = plugin.name
            self._tool_lookup[tool.name] = tool
            self._tool_registrations.setdefault(tool.name, []).append(
                (plugin.name, tool),
            )
        logger.info("[mcp] Registered plugin '%s' with %d tool(s)", plugin.name, len(tools))

    def unregister(self, plugin_name: str) -> None:
        if plugin_name not in self._plugins:
            logger.warning("[mcp] Tried to unregister unknown plugin '%s'", plugin_name)
            return
        self._remove_from_index(plugin_name)
        del self._plugins[plugin_name]
        self._plugin_capabilities.pop(plugin_name, None)
        self._plugin_inspectability.pop(plugin_name, None)
        self._plugin_registration_tool_counts.pop(plugin_name, None)
        self._plugin_modules.pop(plugin_name, None)
        logger.info("[mcp] Unregistered plugin '%s'", plugin_name)

    def get_plugin_module(self, plugin_name: str) -> ModuleType:
        """Return the ``ModuleType`` the orchestrator loaded for ``plugin_name``.

        This is the same module instance the orchestrator dispatches tool
        calls against — `main.py` uses it to invoke per-plugin injection
        seams (``set_token_provider``, ``set_memory_factory``) on the
        correct module so the wiring actually reaches the dispatch path.

        Raises ``KeyError`` when the plugin failed to register or was
        registered via the direct ``register()`` API (no on-disk module
        — tests, the parked builder). Callers in `main.py` should treat
        a missing module as a configuration error and let it propagate;
        a silently-skipped wire is exactly the #153 failure mode this
        seam exists to prevent.
        """
        try:
            return self._plugin_modules[plugin_name]
        except KeyError:
            raise KeyError(
                f"plugin '{plugin_name}' has no loaded module — either "
                f"discovery failed (check `registration_errors`) or the "
                f"plugin was registered without on-disk discovery (tests, "
                f"parked builder)."
            )

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

    @property
    def disabled_plugins_meta(self) -> dict[str, dict]:
        """Metadata for plugins that were scanned but not registered (S2 #470).

        Keyed by plugin name. Each value mirrors the plugins:list card shape
        (status="disabled", enabled=False) so _plugins_snapshot_data can
        render informative cards without the plugin being active.
        """
        return dict(self._disabled_plugins_meta)

    def required_capabilities_for(self, plugin_name: str) -> frozenset[str] | None:
        """The REQUIRED_CAPABILITIES set the named plugin declared at load
        time, or None if it was registered without one (legacy register()
        path; tests)."""
        return self._plugin_capabilities.get(plugin_name)

    def plugin_for_tool(self, tool_name: str) -> str | None:
        """Return the registered plugin that owns the given tool, or None.

        Used by the ACL's new-plugin-flag carve-out (Issue #51) to translate
        a tool name back to its plugin so the per-plugin flag can be looked
        up in SQLite. Stable lookup — backed by the same `_tool_index` that
        routes calls.
        """
        return self._tool_index.get(tool_name)

    def inspectability_for(self, plugin_name: str) -> str | None:
        """The inspectability mark recorded at discovery time (Issue #46).

        Returns ``"inspected"`` for the default text-Python form, ``"trusted"``
        for plugins loaded from ``plugins/_trusted/``, or ``None`` for plugins
        registered directly via ``register()`` (tests / the parked builder
        instance) — those bypass on-disk discovery and therefore have no
        inspectability mark.
        """
        return self._plugin_inspectability.get(plugin_name)

    def supersedes_for(self, tool_name: str) -> dict | None:
        """When the current owner of ``tool_name`` took the tool over from a
        prior plugin, return ``{"tool": tool_name, "from_plugin": <prior>}``.
        Otherwise return ``None`` (no takeover history, or unknown tool).

        Used by the harness UI ``plugins:list`` payload (spec section 5.1)
        to render the "supersedes X from Y" indicator on the takeover
        plugin's card. Derived from ``_tool_registrations`` -- history[-1]
        is the active owner, history[-2] is the immediate predecessor."""
        history = self._tool_registrations.get(tool_name, [])
        if len(history) < 2:
            return None
        prior_plugin, _prior_tool = history[-2]
        return {"tool": tool_name, "from_plugin": prior_plugin}

    def registration_tool_count_for(self, plugin_name: str) -> int:
        """Number of tools the plugin declared at registration time.

        Unlike counting ``_tool_index`` entries by owner, this value is frozen
        at ``register()`` and remains correct even when another plugin (e.g.
        ``google_workspace``) later takes over some or all of the tools.
        Returns 0 for unknown plugin names.
        """
        return self._plugin_registration_tool_counts.get(plugin_name, 0)

    def _remove_from_index(self, plugin_name: str) -> None:
        # Walk every tool's registration history rather than only the tools
        # this plugin currently owns: when a later registrant took over a
        # tool from ``plugin_name``, the index points at the new owner, but
        # the unregister still needs to scrub the now-departing plugin's
        # historical entry so a future re-registration order check stays
        # honest. For each tool, drop entries belonging to ``plugin_name``;
        # if any prior registrant remains, the last surviving entry takes
        # back the active claim (restores the pre-takeover owner) instead
        # of the tool silently vanishing from ``_tool_index`` /
        # ``_tool_lookup``.
        for tool_name in list(self._tool_registrations):
            history = self._tool_registrations[tool_name]
            kept = [(p, t) for (p, t) in history if p != plugin_name]
            if not kept:
                self._tool_registrations.pop(tool_name, None)
                self._tool_index.pop(tool_name, None)
                self._tool_lookup.pop(tool_name, None)
                continue
            self._tool_registrations[tool_name] = kept
            new_owner, new_tool = kept[-1]
            self._tool_index[tool_name] = new_owner
            self._tool_lookup[tool_name] = new_tool

    # ------------------------------------------------------------------
    # Tool access
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        # Use _tool_lookup rather than iterating plugins so that when one
        # plugin takes over a tool from another (e.g. google_workspace
        # superseding gmail / calendar / etc.) each tool name appears exactly
        # once.  _tool_lookup is updated in register() with last-write-wins
        # semantics identical to _tool_index, so its values are always the
        # authoritative Tool objects.
        return list(self._tool_lookup.values())

    async def check_capabilities(
        self,
        tool_name: str,
        capabilities: frozenset[str],
        flags: CallFlags | None,
    ) -> Decision:
        """Resolve the worst Decision across a tool's declared capabilities
        without invoking the tool (Issue #52).

        ``call_tool`` takes exactly one capability per invocation and
        DISPATCHES the tool when the resolved decision is SILENT. That
        makes a per-capability loop unsafe — a SILENT cap would execute
        the tool side-effectfully before the next cap was checked,
        defeating the AND semantics the queue admission overlay relies
        on.

        This method resolves each capability through the ACL/gate pipeline
        (including the post-ACL ``passive`` escalation), takes the worst
        Decision across the set (DENY > ASK > SILENT), and *then* routes
        through the modal (for ``irreversible``) or consent surface (for
        ASK) — at most once per call. The caller (``approve_item``) uses
        the returned Decision to decide whether to dispatch and, if so,
        invokes ``call_tool`` exactly once.

        Returns:
            ``Decision.SILENT`` when every capability resolves silently
            (after any modal/consent acceptance) — the caller may dispatch.
            ``Decision.DENY`` otherwise — the caller must not dispatch.
            ``Decision.ASK`` is never returned from this method: ASK is
            either upgraded to SILENT/DENY by the consent surface, or
            (when no surface is wired) fails closed to DENY.
        """
        tool = self._tool_lookup.get(tool_name)
        if tool is not None and tool.required_capabilities is not None:
            capabilities = tool.required_capabilities
        # Defensive — never silently allow an unknown tool. The queue may
        # hold a tool_name whose plugin was unregistered between add and
        # approve; we must not dispatch it.
        if tool_name not in self._tool_index:
            return Decision.DENY

        # Issue #139 — OR per-tool declaration into the effective flags
        # before any gate/ACL/modal routing reads them. Symmetric with
        # ``call_tool``; the queue's ``approve_item`` path benefits the
        # most from this because the LLM-driven tray-IPC ``call_tool``
        # path at ``cerebral/main.py:1127`` currently bypasses the gate
        # entirely (separate-slice concern, out of scope for #139).
        flags = self._merge_irreversible(flags, tool_name)

        if not capabilities:
            # No declared capabilities → no gate constraint. Mirror
            # call_tool's "capability is None" branch.
            return Decision.SILENT

        # Resolve each capability through ACL/gate (with the passive
        # escalation already applied inside resolve()). Skip the modal
        # and consent surfaces here — we'll route the worst Decision
        # through them at most once below.
        worst = Decision.SILENT
        worst_cap: Capability | None = None
        for cap_str in capabilities:
            try:
                cap = Capability(cap_str)
            except ValueError:
                # An unknown cap on a registered plugin would have been
                # caught at registration time. Defensive fail-closed.
                return Decision.DENY
            if self._acl is not None:
                decision = self._acl.resolve(cap, tool_name, flags)
            else:
                decision = self._gate.check(cap, flags)
            if worst_cap is None or _worse(decision, worst):
                worst = decision
                worst_cap = cap

        # Now route the worst Decision through the modal/consent surfaces
        # exactly once. Mirrors the ordering in call_tool: irreversible
        # routes to the modal regardless of grant (unless already DENY);
        # otherwise ASK routes to the consent surface.
        assert worst_cap is not None  # unreachable: capabilities non-empty
        if flags is not None and flags.irreversible and worst is not Decision.DENY:
            if self._modal is not None:
                worst = await self._modal.request(worst_cap, tool_name, {}, flags)
            else:
                worst = Decision.DENY
        elif worst is Decision.ASK:
            if self._consent is not None:
                worst = await self._consent.request(worst_cap, tool_name, {}, flags)
            else:
                worst = Decision.DENY

        return worst

    def _merge_irreversible(
        self, flags: CallFlags | None, tool_name: str,
    ) -> CallFlags | None:
        """OR the per-tool ``Tool.irreversible`` declaration (Issue #139)
        into the caller-supplied flags.

        Returns ``flags`` unchanged when the tool's declaration is False —
        no allocation in the common case. Returns a fresh ``CallFlags``
        with ``irreversible=True`` when the declaration is True (preserving
        the caller's ``passive`` setting). The merge is one-way: a caller
        that explicitly passes ``irreversible=True`` keeps it.
        """
        tool = self._tool_lookup.get(tool_name)
        if tool is None or not tool.irreversible:
            return flags
        if flags is None:
            return CallFlags(irreversible=True)
        if flags.irreversible:
            return flags
        return CallFlags(passive=flags.passive, irreversible=True)

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
        # Issue #139 — OR per-tool declaration into the effective flags
        # before any gate/ACL/modal routing reads them. Tools that opt in
        # via ``Tool(irreversible=True)`` route through the modal even when
        # the caller passed ``flags=None``.
        flags = self._merge_irreversible(flags, name)
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
            # Issue #49 — irreversible-flagged calls route to a separate
            # modal surface, overriding any SILENT/ASK from the ACL. The
            # gate/ACL is consulted first so an already-DENY decision
            # short-circuits without bothering the user (sharpener #2).
            # When the modal is unwired, irreversible fails closed
            # regardless of grant: an unsupervised Cerebral cannot
            # dispatch an irreversible call.
            if flags is not None and flags.irreversible and decision is not Decision.DENY:
                if self._modal is not None:
                    decision = await self._modal.request(
                        capability, name, args, flags,
                    )
                else:
                    decision = Decision.DENY
            # Issue #48 — ASK (for non-irreversible calls) routes to the
            # tray via the consent surface (when wired). The surface
            # returns SILENT (user allowed) or DENY (user refused,
            # timeout, no subscriber). When no surface is wired we keep
            # the pre-#48 fail-closed behaviour: ASK → DENY.
            elif decision is Decision.ASK and self._consent is not None:
                decision = await self._consent.request(
                    capability, name, args, flags,
                )
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

    def discover_plugins(
        self, plugins_dir: Path, *, disabled: frozenset[str] | None = None
    ) -> None:
        """Import every *.py in plugins_dir, call create(), register the result.

        Conforming layouts (ADR-0005, Issue #46):
          - plugins/<name>.py            (flat, original convention)
          - plugins/<name>/server.py     (subdir, used by the builder for #30)
          - plugins/_trusted/<name>/server.py  (escape hatch — scan skipped,
                                                tray flags as "trusted, unverified")

        Subdirs that don't match any of these (e.g. a folder with no server.py)
        are recorded as ``REASON_NOT_INSPECTABLE_PATH`` so the tray can flag
        the layout error rather than silently hiding the plugin.

        ``disabled`` (S2 #470): plugin names in this set are scanned and
        recorded with full metadata but not registered — their card renders
        as status="disabled" in the Harness UI.
        """
        _disabled = disabled or frozenset()
        if not plugins_dir.is_dir():
            logger.warning("[mcp] plugins_dir '%s' does not exist — skipping discovery", plugins_dir)
            return
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self._load_plugin_file(path, inspectability=INSPECTED, disabled=_disabled)
        for sub in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
            if sub.name.startswith("."):
                continue
            if sub.name == "__pycache__":
                continue
            if sub.name == "_trusted":
                self._discover_trusted_subtree(sub, disabled=_disabled)
                continue
            if sub.name.startswith("_"):
                # Other underscored dirs are reserved scaffolding; ignore.
                continue
            server_py = sub / "server.py"
            if server_py.is_file():
                self._load_plugin_file(server_py, inspectability=INSPECTED, disabled=_disabled)
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

    def _discover_trusted_subtree(
        self, trusted_dir: Path, *, disabled: frozenset[str] = frozenset()
    ) -> None:
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
                self._load_plugin_file(server_py, inspectability=TRUSTED, disabled=disabled)
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

    def _load_plugin_file(
        self,
        path: Path,
        *,
        inspectability: str = INSPECTED,
        disabled: frozenset[str] = frozenset(),
    ) -> None:
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

        # S2 #470 — if the user has disabled this plugin, record its metadata
        # for the Harness UI card but skip registration so no tools are active.
        if plugin_name in disabled:
            tools_summary: list[dict] = []
            try:
                plugin_inst = module.create()
                tools_summary = [
                    {"name": t.name, "description": t.description, "supersedes": None}
                    for t in plugin_inst.list_tools()
                ]
            except Exception:
                pass  # partial metadata is fine; tools stays []
            self._disabled_plugins_meta[plugin_name] = {
                "name": plugin_name,
                "path": str(path),
                "inspectability": inspectability,
                "capabilities": sorted(required),
                "tools": tools_summary,
            }
            logger.info("[mcp] Plugin '%s' is disabled — scanned but not registered", plugin_name)
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
        # Issue #153 — record the loaded module so main.py can target the
        # correct module instance for set_token_provider / set_memory_factory
        # wiring. Keyed by plugin.name (the orchestrator-canonical name),
        # NOT path.stem — these usually match but a plugin is free to set
        # PLUGIN_NAME to something else.
        self._plugin_modules[plugin.name] = module

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
