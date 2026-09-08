"""
Settings-control plugin -- F4 (#327).

Exposes ONE tool, ``set_system_setting(key, value)``, so the user can say
"Felix, turn TTS volume to 50%" or "use the Light theme" and have Felix
apply it through the planner / ADR-0008 active loop.

ADR-0005 gating
---------------
The plugin declares ``REQUIRED_CAPABILITIES = {"fs_write"}``. FS_WRITE is
ASK-class in the day-1 policy, so the orchestrator routes a non-passive
call ("Felix, change X" through the planner) to the consent surface and
the user sees an inline consent card. The setting is applied only after
the user accepts (Once / Session / Persistent). A deny short-circuits
before this plugin's ``call_tool`` ever runs.

Scope (matches issue #327)
--------------------------
* **System settings owned by Cerebral** (``cerebral/data/felix-settings.json``):
  ``notifications_enabled``, ``reminder_interval_minutes``, ``camera_enabled``,
  ``visualiser_visible``, ``mic_mode``, ``tts_muted``, ``tts_volume``,
  ``mic_input_device``. Applied via the SettingsStore singleton and
  reflected live through the existing ``settings_updated`` broadcast.
* **Appearance settings owned by the renderer** (theme/scale/accent live
  in ``localStorage`` under ``om:appearance``): the tool emits an
  ``apply_appearance`` broadcast that the renderer handles and persists
  to localStorage. Cerebral does NOT mirror these to disk; the renderer
  is the single source of truth.

Out of scope: profile-scoped state (voice, wake name, memory) -- the
issue explicitly excludes those.

Wiring seam
-----------
``set_apply_callback(fn)`` is the module-level seam ``cerebral.main``
invokes via ``_wire_plugin_seams`` (see Issue #153 for why we target the
orchestrator-loaded module instance rather than ``import plugins.X``).
The injected callback is an async function ``(key, value) -> None`` that
performs the actual apply + broadcast. Until it is wired, the tool fails
with a descriptive error so an unscaffolded process can't silently
no-op a settings change.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "settings_control"

# FS_WRITE: ASK-class in DEFAULT_POLICY -- a non-passive call routes to the
# consent surface. Felix writes felix-settings.json (or broadcasts an
# appearance change the renderer persists), so fs_write is the honest
# capability claim.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_write"})

# Cerebral-owned keys (mirror cerebral.settings._DEFAULTS).
SYSTEM_KEYS: frozenset[str] = frozenset({
    "notifications_enabled",
    "reminder_interval_minutes",
    "camera_enabled",
    "visualiser_visible",
    "mic_mode",
    "tts_muted",
    "tts_volume",
    "mic_input_device",
})

# Renderer-owned appearance keys (localStorage source of truth).
APPEARANCE_KEYS: frozenset[str] = frozenset({
    "ui_scale",
    "ui_theme",
    "ui_accent",
})

ALLOWED_KEYS: frozenset[str] = SYSTEM_KEYS | APPEARANCE_KEYS


# Module-level seam injected by cerebral.main._wire_plugin_seams.
# Shape: async fn(key: str, value: Any) -> None.
ApplyFn = Callable[[str, Any], Awaitable[None]]
_apply_fn: Optional[ApplyFn] = None


def set_apply_callback(fn: Optional[ApplyFn]) -> None:
    """Inject the (async) apply callback from cerebral.main."""
    global _apply_fn
    _apply_fn = fn


# Module-level seam for plugin_set_enabled (ADR-0035 slice H).
# Shape: async fn(plugin_name: str, enabled: bool) -> str; raises ValueError
# on failure, else returns a human-readable success message.
PluginEnableFn = Callable[[str, bool], Awaitable[str]]
_plugin_enable_fn: Optional[PluginEnableFn] = None


def set_plugin_enable_callback(fn: Optional[PluginEnableFn]) -> None:
    """Inject the (async) plugin-enable callback from cerebral.main."""
    global _plugin_enable_fn
    _plugin_enable_fn = fn


class SettingsControlPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="set_system_setting",
                description=(
                    "Change a Felix system setting (TTS volume/mute, mic mode, "
                    "notifications, reminder interval, camera, visualiser, mic "
                    "input device, UI scale/theme/accent). Requires user "
                    "approval via the consent card; the change applies only "
                    "after the user accepts."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "enum": sorted(ALLOWED_KEYS),
                            "description": "Which system setting to change.",
                        },
                        "value": {
                            "description": (
                                "New value -- bool for toggles "
                                "(notifications_enabled, camera_enabled, "
                                "visualiser_visible, tts_muted); int for "
                                "tts_volume (0-100) and "
                                "reminder_interval_minutes (>=0); string for "
                                "mic_mode (passive|ptt|disabled), "
                                "mic_input_device (label, '' for system "
                                "default), ui_theme (midnight|light|hc), "
                                "ui_scale (e.g. '1', '0.9', '1.25'), "
                                "ui_accent (CSS hex like '#7c5cfc')."
                            ),
                        },
                    },
                    "required": ["key", "value"],
                },
            ),
            Tool(
                name="plugin_set_enabled",
                description=(
                    "Enable or disable an installed plugin. Requires user "
                    "approval via the consent card; the change applies only "
                    "after the user accepts."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "plugin_name": {
                            "type": "string",
                            "description": "Name of the plugin to enable/disable.",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "True to enable, false to disable.",
                        },
                    },
                    "required": ["plugin_name", "enabled"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "plugin_set_enabled":
            return await self._plugin_set_enabled(args)
        if tool_name != "set_system_setting":
            return ToolResult(
                content=f"Unknown tool: '{tool_name}'", is_error=True,
            )
        key = args.get("key")
        if not isinstance(key, str) or key not in ALLOWED_KEYS:
            return ToolResult(
                content=(
                    f"Unsupported setting key: {key!r}. "
                    f"Allowed: {sorted(ALLOWED_KEYS)}"
                ),
                is_error=True,
            )
        if "value" not in args:
            return ToolResult(
                content=f"Missing 'value' for setting {key!r}",
                is_error=True,
            )
        value = args["value"]
        if _apply_fn is None:
            logger.warning(
                "[settings_control] apply callback not wired; cannot set %r", key,
            )
            return ToolResult(
                content="Settings control is not wired in this process.",
                is_error=True,
            )
        try:
            await _apply_fn(key, value)
        except ValueError as exc:
            return ToolResult(
                content=f"Could not change {key!r}: {exc}", is_error=True,
            )
        except Exception as exc:  # pragma: no cover -- defensive
            logger.exception("[settings_control] apply failed for %r", key)
            return ToolResult(
                content=f"Could not change {key!r}: {exc}", is_error=True,
            )
        return ToolResult(content=f"Set {key} to {value!r}.")

    async def _plugin_set_enabled(self, args: dict) -> ToolResult:
        plugin_name = args.get("plugin_name")
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            return ToolResult(
                content="Missing or invalid 'plugin_name'.", is_error=True,
            )
        enabled = args.get("enabled")
        if not isinstance(enabled, bool):
            return ToolResult(
                content=f"'enabled' must be a boolean, got {type(enabled).__name__}.",
                is_error=True,
            )
        if _plugin_enable_fn is None:
            logger.warning(
                "[settings_control] plugin-enable callback not wired; cannot set %r",
                plugin_name,
            )
            return ToolResult(
                content="Settings control is not wired in this process.",
                is_error=True,
            )
        try:
            message = await _plugin_enable_fn(plugin_name, enabled)
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:  # pragma: no cover -- defensive
            logger.exception(
                "[settings_control] plugin enable/disable failed for %r", plugin_name,
            )
            return ToolResult(
                content=f"Could not change plugin {plugin_name!r}: {exc}",
                is_error=True,
            )
        return ToolResult(content=message)


def create() -> SettingsControlPlugin:
    return SettingsControlPlugin()
