'use strict';

// Tray-side Permissions store (Issue #53, ADR-0005).
//
// Dual-mode export (Issue #202): the same source file feeds the Node
// unit-test suite via require() AND the Main window's renderer via a
// plain <script src> tag (the Main window has nodeIntegration: false
// per ADR-0007 so it can't require()). The UMD-ish wrapper at the
// bottom checks for module.exports and otherwise exposes the module on
// window.PermissionsStoreMod. No logic differences between the two
// load paths — the wrapper is mechanical.
//
// Bridges the `permissions_state` event from Cerebral to the Permissions
// pane's renderer. The store is UI-agnostic so it can be unit-tested
// without Electron — the Main window's renderer wires the WebSocket
// send callback and reads / mutates state through the same surface.
//
// State shape (mirrors the Python side):
//   profile_id:                int | null
//   capability_vocabulary:     [{value, label, description, default}]
//   class_defaults:            {capability: 'silent'|'ask'|'deny'}
//   persistent_class_grants:   {capability: 'silent'|'ask'|'deny'}
//   persistent_tool_overrides: {tool_name: 'silent'|'ask'|'deny'}
//   session_class_grants:      {capability: 'silent'|'ask'|'deny'}
//   shell_exec_unlocked:       bool
//
// Plus, sourced from existing tray events:
//   tools:                    [{name, description, plugin}]
//   plugins_with_new_flag:    [{name}]
//
// Mutations route to Cerebral via the injected `send` callback — the
// store itself never touches IPC directly. Cerebral's response is the
// authoritative permissions_state event, which the store applies via
// applyState(). No optimistic local updates: the round-trip is fast
// enough and "what Cerebral says" is the only truth that matters for
// security state.

const VALID_DECISIONS         = new Set(['silent', 'ask', 'deny']);
const VALID_TOOL_OVERRIDES    = new Set(['silent', 'ask', 'deny', 'inherit']);

const DEFAULT_STATE = {
  profile_id:                null,
  capability_vocabulary:     [],
  class_defaults:            {},
  persistent_class_grants:   {},
  persistent_tool_overrides: {},
  session_class_grants:      {},
  shell_exec_unlocked:       false,
};

class PermissionsStore {
  constructor({ send, onChange } = {}) {
    this._send       = send       || (() => {});
    this._onChange   = onChange   || (() => {});
    this._state      = { ...DEFAULT_STATE };
    this._tools      = [];                  // from `tools_list`
    this._plugins    = [];                  // from `plugins_list`
  }

  // ── State accessors ──────────────────────────────────────────────────

  get state()   { return this._state; }
  get tools()   { return this._tools; }
  get plugins() { return this._plugins; }

  // Returns the effective policy for a capability class:
  //   persistent override > session grant > snapshot default
  // Used by the renderer to highlight the active toggle without
  // re-implementing the resolution rules. Note this does NOT include
  // per-tool overrides — those are surfaced separately on the Tools tab.
  effectiveClassPolicy(capability) {
    const persistent = this._state.persistent_class_grants[capability];
    if (persistent) return persistent;
    const session = this._state.session_class_grants[capability];
    if (session) return session;
    return this._state.class_defaults[capability] || null;
  }

  // Returns the per-tool override decision (or 'inherit' when none set).
  effectiveToolOverride(toolName) {
    return this._state.persistent_tool_overrides[toolName] || 'inherit';
  }

  // List of plugins still carrying the new-plugin flag from #51.
  // The Capabilities tab renders one "Trust this plugin" row each.
  flaggedPlugins() {
    return this._plugins.filter(p => p && p.new_plugin_flag);
  }

  // Filter the tools list by a case-insensitive substring match against
  // tool name OR plugin name (sharpener pin #4).
  filterTools(query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return this._tools.slice();
    return this._tools.filter(t => {
      const name   = String(t.name   || '').toLowerCase();
      const plugin = String(t.plugin || '').toLowerCase();
      return name.includes(q) || plugin.includes(q);
    });
  }

  // ── Inbound: Cerebral broadcasts ─────────────────────────────────────

  applyState(payload) {
    if (!payload || typeof payload !== 'object') return;
    this._state = {
      profile_id:                payload.profile_id ?? null,
      capability_vocabulary:     Array.isArray(payload.capability_vocabulary)
                                   ? payload.capability_vocabulary : [],
      class_defaults:            payload.class_defaults            || {},
      persistent_class_grants:   payload.persistent_class_grants   || {},
      persistent_tool_overrides: payload.persistent_tool_overrides || {},
      session_class_grants:      payload.session_class_grants      || {},
      shell_exec_unlocked:       Boolean(payload.shell_exec_unlocked),
    };
    this._onChange();
  }

  applyToolsList(tools) {
    this._tools = Array.isArray(tools) ? tools.slice() : [];
    this._onChange();
  }

  applyPluginsList(plugins) {
    this._plugins = Array.isArray(plugins) ? plugins.slice() : [];
    this._onChange();
  }

  // ── Outbound: user actions on the Permissions window ─────────────────

  // Capabilities tab — toggle for one of the 16 classes. Validates the
  // decision verb so a typo in the renderer can't write garbage to the
  // Cerebral side. shell_exec writes are still allowed here — the
  // server is the authority on whether the row is unlocked yet, and
  // refuses politely with a no-op when locked. The renderer mirrors
  // the lock status to keep the toggle visually disabled.
  setClassPolicy(capability, decision) {
    if (!capability || !VALID_DECISIONS.has(decision)) return false;
    this._send({
      type: 'set_class_policy',
      data: { capability, decision },
    });
    return true;
  }

  revokeClassPolicy(capability) {
    if (!capability) return false;
    this._send({
      type: 'revoke_class_policy',
      data: { capability },
    });
    return true;
  }

  // Tools tab — per-tool override dropdown. 'inherit' clears the row
  // (revoke_tool_override on the server); the others write a
  // scope='tool' row.
  setToolOverride(toolName, decision) {
    if (!toolName || !VALID_TOOL_OVERRIDES.has(decision)) return false;
    this._send({
      type: 'set_tool_override',
      data: { tool: toolName, decision },
    });
    return true;
  }

  // Capabilities tab — Revoke button on a session-grant row.
  revokeSessionGrant(capability) {
    if (!capability) return false;
    this._send({
      type: 'revoke_session_grant',
      data: { capability },
    });
    return true;
  }

  // Capabilities tab — one-time confirmation modal accepted; flip the
  // shell_exec lock on the active profile. One-way; no re-locker.
  unlockShellExec() {
    this._send({ type: 'unlock_shell_exec' });
    return true;
  }

  // Capabilities tab — "Trust this plugin" button next to a plugin
  // still carrying the new-plugin flag from #51.
  clearNewPluginFlag(pluginName) {
    if (!pluginName) return false;
    this._send({
      type: 'clear_new_plugin_flag',
      data: { name: pluginName },
    });
    return true;
  }

  // Refresh-on-open: pull the latest state from Cerebral. Used by
  // main.js when the Permissions window is opened so a stale store
  // doesn't render across a profile switch the window missed.
  requestRefresh() {
    this._send({ type: 'list_permissions' });
    this._send({ type: 'list_tools' });
    this._send({ type: 'list_plugins' });
  }
}

// ── Dual-mode export (Issue #202) ────────────────────────────────────
// Node (tests): module.exports = { ... }
// Browser (Main window renderer): window.PermissionsStoreMod = { ... }
const _exports = {
  PermissionsStore,
  VALID_DECISIONS,
  VALID_TOOL_OVERRIDES,
  DEFAULT_STATE,
};

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.PermissionsStoreMod = _exports;
}
