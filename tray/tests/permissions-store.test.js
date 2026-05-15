'use strict';

// Tray-side Permissions store tests — Issue #53.
//
// The PermissionsStore is the JS half of the Permissions UI:
//   1. consumes `permissions_state`, `tools_list`, `plugins_list`
//      events from Cerebral
//   2. exposes effective-policy accessors so the renderer doesn't
//      reimplement the resolution rules
//   3. routes user actions back to Cerebral as the matching IPC verbs
//      (set_class_policy, set_tool_override, revoke_session_grant,
//      unlock_shell_exec, clear_new_plugin_flag, list_permissions)

const {
  PermissionsStore,
  VALID_DECISIONS,
  VALID_TOOL_OVERRIDES,
  DEFAULT_STATE,
} = require('../lib/permissions-store');

function fixtureState(overrides = {}) {
  return {
    profile_id: 1,
    capability_vocabulary: [
      { value: 'fs_read',  label: 'Read files on disk',  description: 'Read.', default: 'silent' },
      { value: 'fs_write', label: 'Write files on disk', description: 'Write.', default: 'ask' },
      { value: 'shell_exec', label: 'Run a shell command', description: 'Run.', default: 'deny' },
    ],
    class_defaults: {
      fs_read: 'silent',
      fs_write: 'ask',
      shell_exec: 'deny',
    },
    persistent_class_grants:   {},
    persistent_tool_overrides: {},
    session_class_grants:      {},
    shell_exec_unlocked:       false,
    ...overrides,
  };
}

let send, onChange, store;

beforeEach(() => {
  send = jest.fn();
  onChange = jest.fn();
  store = new PermissionsStore({ send, onChange });
});

// ── Cycle 1: vocabulary constants ─────────────────────────────────────────────

test('VALID_DECISIONS contains exactly silent/ask/deny', () => {
  expect(Array.from(VALID_DECISIONS).sort()).toEqual(['ask', 'deny', 'silent']);
});

test('VALID_TOOL_OVERRIDES extends VALID_DECISIONS with inherit', () => {
  expect(Array.from(VALID_TOOL_OVERRIDES).sort()).toEqual(
    ['ask', 'deny', 'inherit', 'silent'],
  );
});

test('DEFAULT_STATE has all expected keys', () => {
  expect(Object.keys(DEFAULT_STATE).sort()).toEqual([
    'capability_vocabulary',
    'class_defaults',
    'persistent_class_grants',
    'persistent_tool_overrides',
    'profile_id',
    'session_class_grants',
    'shell_exec_unlocked',
  ]);
});

// ── Cycle 2: applyState replaces the snapshot and notifies ───────────────────

test('applyState replaces the state and triggers onChange', () => {
  store.applyState(fixtureState());
  expect(store.state.profile_id).toBe(1);
  expect(store.state.capability_vocabulary).toHaveLength(3);
  expect(onChange).toHaveBeenCalledTimes(1);
});

test('applyState ignores null payloads', () => {
  store.applyState(null);
  store.applyState(undefined);
  store.applyState('not-an-object');
  expect(store.state.profile_id).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});

test('applyState defends against missing nested fields', () => {
  store.applyState({ profile_id: 7 });
  // Missing fields default to empty containers; no crash.
  expect(store.state.profile_id).toBe(7);
  expect(store.state.capability_vocabulary).toEqual([]);
  expect(store.state.class_defaults).toEqual({});
  expect(store.state.persistent_class_grants).toEqual({});
  expect(store.state.persistent_tool_overrides).toEqual({});
  expect(store.state.session_class_grants).toEqual({});
  expect(store.state.shell_exec_unlocked).toBe(false);
});

// ── Cycle 3: tools + plugins side channels ───────────────────────────────────

test('applyToolsList stores the list and triggers onChange', () => {
  const tools = [
    { name: 'files.write_journal', plugin: 'files', description: '' },
    { name: 'browser.fetch',       plugin: 'browser', description: '' },
  ];
  store.applyToolsList(tools);
  expect(store.tools).toEqual(tools);
  expect(onChange).toHaveBeenCalled();
});

test('applyToolsList accepts a non-array as empty', () => {
  store.applyToolsList(null);
  expect(store.tools).toEqual([]);
});

test('applyPluginsList stores the list', () => {
  const plugins = [
    { name: 'files',  new_plugin_flag: false },
    { name: 'maybe',  new_plugin_flag: true  },
  ];
  store.applyPluginsList(plugins);
  expect(store.plugins).toEqual(plugins);
});

// ── Cycle 4: effective policy accessors ──────────────────────────────────────

test('effectiveClassPolicy returns the snapshot default with no overrides', () => {
  store.applyState(fixtureState());
  expect(store.effectiveClassPolicy('fs_read')).toBe('silent');
  expect(store.effectiveClassPolicy('fs_write')).toBe('ask');
});

test('effectiveClassPolicy: persistent grant beats default', () => {
  store.applyState(fixtureState({
    persistent_class_grants: { fs_write: 'silent' },
  }));
  expect(store.effectiveClassPolicy('fs_write')).toBe('silent');
});

test('effectiveClassPolicy: session grant used when no persistent grant', () => {
  store.applyState(fixtureState({
    session_class_grants: { fs_write: 'silent' },
  }));
  expect(store.effectiveClassPolicy('fs_write')).toBe('silent');
});

test('effectiveClassPolicy: persistent grant beats session', () => {
  store.applyState(fixtureState({
    persistent_class_grants: { fs_write: 'deny' },
    session_class_grants:    { fs_write: 'silent' },
  }));
  expect(store.effectiveClassPolicy('fs_write')).toBe('deny');
});

test('effectiveClassPolicy returns null for unknown capability', () => {
  store.applyState(fixtureState());
  expect(store.effectiveClassPolicy('not_a_class')).toBeNull();
});

test('effectiveToolOverride returns inherit when no row', () => {
  store.applyState(fixtureState());
  expect(store.effectiveToolOverride('files.write_journal')).toBe('inherit');
});

test('effectiveToolOverride returns the stored row when present', () => {
  store.applyState(fixtureState({
    persistent_tool_overrides: { 'files.write_journal': 'deny' },
  }));
  expect(store.effectiveToolOverride('files.write_journal')).toBe('deny');
});

// ── Cycle 5: filterTools ──────────────────────────────────────────────────────

test('filterTools returns all tools on empty query', () => {
  store.applyToolsList([
    { name: 'files.write', plugin: 'files', description: '' },
    { name: 'shell.run',   plugin: 'shell', description: '' },
  ]);
  expect(store.filterTools('').length).toBe(2);
  expect(store.filterTools('   ').length).toBe(2);
});

test('filterTools matches on tool name (case-insensitive)', () => {
  store.applyToolsList([
    { name: 'files.write', plugin: 'files', description: '' },
    { name: 'shell.run',   plugin: 'shell', description: '' },
  ]);
  expect(store.filterTools('FILES').map(t => t.name)).toEqual(['files.write']);
});

test('filterTools matches on plugin name', () => {
  store.applyToolsList([
    { name: 'files.write', plugin: 'files', description: '' },
    { name: 'foo.bar',     plugin: 'shell', description: '' },
  ]);
  expect(store.filterTools('shell').map(t => t.name)).toEqual(['foo.bar']);
});

test('filterTools handles tools missing optional fields', () => {
  store.applyToolsList([
    { name: 'foo' },
    { plugin: 'bar' },
  ]);
  expect(store.filterTools('foo').length).toBe(1);
  expect(store.filterTools('bar').length).toBe(1);
});

// ── Cycle 6: flaggedPlugins ──────────────────────────────────────────────────

test('flaggedPlugins lists only plugins with new_plugin_flag=true', () => {
  store.applyPluginsList([
    { name: 'files',  new_plugin_flag: false },
    { name: 'foo',    new_plugin_flag: true  },
    { name: 'bar',    new_plugin_flag: true  },
  ]);
  expect(store.flaggedPlugins().map(p => p.name).sort()).toEqual(['bar', 'foo']);
});

test('flaggedPlugins ignores nullish entries gracefully', () => {
  store.applyPluginsList([null, { name: 'x', new_plugin_flag: true }]);
  expect(store.flaggedPlugins().map(p => p.name)).toEqual(['x']);
});

// ── Cycle 7: setClassPolicy + revokeClassPolicy ──────────────────────────────

test('setClassPolicy sends set_class_policy with capability+decision', () => {
  expect(store.setClassPolicy('fs_write', 'silent')).toBe(true);
  expect(send).toHaveBeenCalledWith({
    type: 'set_class_policy',
    data: { capability: 'fs_write', decision: 'silent' },
  });
});

test('setClassPolicy refuses unknown decision', () => {
  expect(store.setClassPolicy('fs_write', 'bogus')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

test('setClassPolicy refuses missing capability', () => {
  expect(store.setClassPolicy('', 'silent')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

test('setClassPolicy refuses inherit (only valid for tool overrides)', () => {
  expect(store.setClassPolicy('fs_write', 'inherit')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

test('revokeClassPolicy sends revoke_class_policy', () => {
  expect(store.revokeClassPolicy('fs_write')).toBe(true);
  expect(send).toHaveBeenCalledWith({
    type: 'revoke_class_policy',
    data: { capability: 'fs_write' },
  });
});

// ── Cycle 8: setToolOverride ──────────────────────────────────────────────────

test('setToolOverride sends set_tool_override with tool+decision', () => {
  expect(store.setToolOverride('browser.fetch', 'deny')).toBe(true);
  expect(send).toHaveBeenCalledWith({
    type: 'set_tool_override',
    data: { tool: 'browser.fetch', decision: 'deny' },
  });
});

test('setToolOverride accepts inherit for clearing', () => {
  expect(store.setToolOverride('browser.fetch', 'inherit')).toBe(true);
  expect(send).toHaveBeenCalledWith({
    type: 'set_tool_override',
    data: { tool: 'browser.fetch', decision: 'inherit' },
  });
});

test('setToolOverride refuses unknown decision', () => {
  expect(store.setToolOverride('browser.fetch', 'maybe')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

test('setToolOverride refuses missing tool name', () => {
  expect(store.setToolOverride('', 'silent')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

// ── Cycle 9: revokeSessionGrant ──────────────────────────────────────────────

test('revokeSessionGrant sends the IPC', () => {
  expect(store.revokeSessionGrant('fs_write')).toBe(true);
  expect(send).toHaveBeenCalledWith({
    type: 'revoke_session_grant',
    data: { capability: 'fs_write' },
  });
});

test('revokeSessionGrant refuses missing capability', () => {
  expect(store.revokeSessionGrant('')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

// ── Cycle 10: unlockShellExec ─────────────────────────────────────────────────

test('unlockShellExec sends an unlock IPC with no payload', () => {
  expect(store.unlockShellExec()).toBe(true);
  expect(send).toHaveBeenCalledWith({ type: 'unlock_shell_exec' });
});

// ── Cycle 11: clearNewPluginFlag ──────────────────────────────────────────────

test('clearNewPluginFlag sends the IPC with the plugin name', () => {
  expect(store.clearNewPluginFlag('weatherbug')).toBe(true);
  expect(send).toHaveBeenCalledWith({
    type: 'clear_new_plugin_flag',
    data: { name: 'weatherbug' },
  });
});

test('clearNewPluginFlag refuses empty plugin name', () => {
  expect(store.clearNewPluginFlag('')).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

// ── Cycle 12: requestRefresh sends list_* trio ───────────────────────────────

test('requestRefresh sends list_permissions, list_tools, list_plugins', () => {
  store.requestRefresh();
  const types = send.mock.calls.map(c => c[0].type);
  expect(types).toEqual(['list_permissions', 'list_tools', 'list_plugins']);
});

// ── Cycle 13: round-trip — server replies with updated state ─────────────────

test('toggle a class policy then reapply state reflects the change', () => {
  store.applyState(fixtureState());
  expect(store.effectiveClassPolicy('fs_write')).toBe('ask');
  store.setClassPolicy('fs_write', 'silent');
  // Server echoes the change via permissions_state — store applies it.
  store.applyState(fixtureState({
    persistent_class_grants: { fs_write: 'silent' },
  }));
  expect(store.effectiveClassPolicy('fs_write')).toBe('silent');
});

test('profile switch reloads state and clears session grants', () => {
  store.applyState(fixtureState({
    profile_id: 1,
    persistent_class_grants: { fs_write: 'silent' },
    session_class_grants:    { fs_read:  'deny'   },
  }));
  // Server broadcasts the new profile's state — old session grants gone.
  store.applyState(fixtureState({ profile_id: 2 }));
  expect(store.state.profile_id).toBe(2);
  expect(store.state.persistent_class_grants).toEqual({});
  expect(store.state.session_class_grants).toEqual({});
});
