'use strict';

// Unit tests for tray/lib/harness-panel.js -- S3 (#471).
// Covers: card rendering, error cards, drawer, filter rail, applyFilters, renderGrid.

const HP = require('../lib/harness-panel');

// ── fixtures ─────────────────────────────────────────────────────────────────

function makePlugin(overrides) {
  return Object.assign({
    name:          'google_workspace',
    status:        'active',
    trust:         'inspected',
    source_layout: 'flat',
    path:          'plugins/google_workspace.py',
    capabilities:  ['network', 'credentials_read'],
    enabled:       true,
    tools: [
      { name: 'gmail_send', description: 'Send an email via Gmail', supersedes: null },
    ],
    credentials: [
      { provider: 'google', source: 'keyring', hint: '****3f2a', env_var: null },
    ],
  }, overrides || {});
}

function makeError(overrides) {
  return Object.assign({
    plugin_name: 'broken_thing',
    reason:      'REASON_NOT_INSPECTABLE_PATH',
    detail:      'subdir without server.py',
    path:        'plugins/broken_thing/',
  }, overrides || {});
}

function makeFilters(overrides) {
  return Object.assign({ capabilities: [], statuses: [] }, overrides || {});
}

// ── escHtml ──────────────────────────────────────────────────────────────────

describe('escHtml', () => {
  test('escapes & < > "', () => {
    expect(HP.escHtml('<b>&"test"</b>')).toBe('&lt;b&gt;&amp;&quot;test&quot;&lt;/b&gt;');
  });

  test('returns empty string for null/undefined', () => {
    expect(HP.escHtml(null)).toBe('');
    expect(HP.escHtml(undefined)).toBe('');
  });
});

// ── capabilityIcon ────────────────────────────────────────────────────────────

test('capabilityIcon returns icon for known cap', () => {
  expect(typeof HP.capabilityIcon('network')).toBe('string');
  expect(HP.capabilityIcon('network').length).toBeGreaterThan(0);
});

test('capabilityIcon falls back to wrench for unknown cap', () => {
  expect(HP.capabilityIcon('unknown_cap_xyz')).toBe('🔧');
});

// ── makeCard ──────────────────────────────────────────────────────────────────

describe('makeCard', () => {
  test('returns empty for null/missing name', () => {
    expect(HP.makeCard(null)).toBe('');
    expect(HP.makeCard({ })).toBe('');
  });

  test('includes plugin name', () => {
    expect(HP.makeCard(makePlugin())).toContain('google_workspace');
  });

  test('includes status dot class', () => {
    expect(HP.makeCard(makePlugin({ status: 'active' }))).toContain('hrns-dot-active');
    expect(HP.makeCard(makePlugin({ status: 'disabled' }))).toContain('hrns-dot-disabled');
  });

  test('disabled card has reduced-opacity class', () => {
    expect(HP.makeCard(makePlugin({ status: 'disabled' }))).toContain('hrns-card-disabled');
    expect(HP.makeCard(makePlugin({ status: 'active' }))).not.toContain('hrns-card-disabled');
  });

  test('trusted badge shown when trust===trusted, hidden otherwise', () => {
    const trustedHtml   = HP.makeCard(makePlugin({ trust: 'trusted' }));
    const inspectedHtml = HP.makeCard(makePlugin({ trust: 'inspected' }));
    expect(trustedHtml).toContain('hrns-badge-trusted');
    expect(trustedHtml).toContain('trusted, unverified');
    expect(inspectedHtml).not.toContain('hrns-badge-trusted');
  });

  test('tool count in card', () => {
    const html = HP.makeCard(makePlugin({ tools: [{name:'a',description:'b',supersedes:null},{name:'c',description:'d',supersedes:null}] }));
    expect(html).toContain('2 tools');
  });

  test('singular tool for count 1', () => {
    const html = HP.makeCard(makePlugin());
    expect(html).toContain('1 tool');
    expect(html).not.toContain('1 tools');
  });

  test('shows source layout label', () => {
    expect(HP.makeCard(makePlugin({ source_layout: 'flat' }))).toContain('flat');
    expect(HP.makeCard(makePlugin({ source_layout: 'subdir' }))).toContain('subdir');
  });

  test('shows up to 3 capability tags', () => {
    const caps = ['network', 'filesystem_read', 'credentials_read', 'exec', 'ui'];
    const html = HP.makeCard(makePlugin({ capabilities: caps }));
    expect(html).toContain('network');
    expect(html).toContain('filesystem_read');
    expect(html).toContain('credentials_read');
    expect(html).toContain('+2');  // overflow for the remaining 2
  });

  test('no overflow shown when <=3 caps', () => {
    const html = HP.makeCard(makePlugin({ capabilities: ['network', 'exec'] }));
    expect(html).not.toContain('+');
  });

  test('data-plugin attribute set to plugin name', () => {
    expect(HP.makeCard(makePlugin({ name: 'todoist' }))).toContain('data-plugin="todoist"');
  });

  test('escapes HTML in plugin name', () => {
    const html = HP.makeCard(makePlugin({ name: '<evil>' }));
    expect(html).not.toContain('<evil>');
    expect(html).toContain('&lt;evil&gt;');
  });
});

// ── makeErrorCard ─────────────────────────────────────────────────────────────

describe('makeErrorCard', () => {
  test('returns empty for null', () => {
    expect(HP.makeErrorCard(null)).toBe('');
  });

  test('includes plugin_name', () => {
    expect(HP.makeErrorCard(makeError())).toContain('broken_thing');
  });

  test('includes reason string', () => {
    expect(HP.makeErrorCard(makeError())).toContain('REASON_NOT_INSPECTABLE_PATH');
  });

  test('has error status dot', () => {
    expect(HP.makeErrorCard(makeError())).toContain('hrns-dot-error');
  });

  test('has hrns-card-error class', () => {
    expect(HP.makeErrorCard(makeError())).toContain('hrns-card-error');
  });

  test('data-error-plugin attribute set', () => {
    expect(HP.makeErrorCard(makeError({ plugin_name: 'my_plugin' }))).toContain('data-error-plugin="my_plugin"');
  });
});

// ── makeDrawer ────────────────────────────────────────────────────────────────

describe('makeDrawer', () => {
  test('returns empty for null', () => {
    expect(HP.makeDrawer(null)).toBe('');
  });

  test('includes plugin name in header', () => {
    expect(HP.makeDrawer(makePlugin())).toContain('google_workspace');
  });

  test('trusted badge in drawer header', () => {
    const html = HP.makeDrawer(makePlugin({ trust: 'trusted' }));
    expect(html).toContain('hrns-badge-trusted');
    expect(html).toContain('trusted, unverified');
  });

  test('status label in header', () => {
    expect(HP.makeDrawer(makePlugin({ status: 'active' }))).toContain('active');
  });

  test('live enable/disable toggle present (S4 #472)', () => {
    // S3 shipped a disabled placeholder; S4 replaced it with a live toggle
    // wired to plugins:set_enabled.
    const html = HP.makeDrawer(makePlugin({ name: 'google_workspace', enabled: true }));
    expect(html).toContain('hrns-toggle');
    expect(html).toContain('data-toggle-plugin="google_workspace"');
    // Enabled plugin's toggle targets enabled=false (i.e. Disable).
    expect(html).toContain('data-target-enabled="false"');
    expect(html).toContain('Disable');
  });

  test('toggle label + target flip when plugin already disabled', () => {
    const html = HP.makeDrawer(makePlugin({
      name: 'notes', status: 'disabled', enabled: false,
    }));
    expect(html).toContain('data-target-enabled="true"');
    expect(html).toContain('Enable');
  });

  test('tools section contains tool name and description', () => {
    const html = HP.makeDrawer(makePlugin());
    expect(html).toContain('gmail_send');
    expect(html).toContain('Send an email via Gmail');
  });

  test('supersedes indicator shown when present', () => {
    const plugin = makePlugin({ tools: [{
      name: 'gmail_send',
      description: 'Send email',
      supersedes: { tool: 'gmail_send', from_plugin: 'gmail' },
    }]});
    const html = HP.makeDrawer(plugin);
    expect(html).toContain('hrns-supersedes');
    expect(html).toContain('gmail');
  });

  test('capabilities section has cap links', () => {
    const html = HP.makeDrawer(makePlugin({ capabilities: ['network', 'credentials_read'] }));
    expect(html).toContain('data-filter-cap="network"');
    expect(html).toContain('data-filter-cap="credentials_read"');
  });

  test('credentials section shows provider, source, hint', () => {
    const html = HP.makeDrawer(makePlugin());
    expect(html).toContain('google');
    expect(html).toContain('keyring');
    expect(html).toContain('****3f2a');
  });

  test('credentials show env_var when source is env', () => {
    const plugin = makePlugin({
      credentials: [{ provider: 'myapi', source: 'env', hint: null, env_var: 'MYAPI_KEY' }]
    });
    const html = HP.makeDrawer(plugin);
    expect(html).toContain('MYAPI_KEY');
    expect(html).toContain('env:');
  });

  test('credentials Manage button present', () => {
    const html = HP.makeDrawer(makePlugin());
    expect(html).toContain('hrns-cred-manage');
    expect(html).toContain('Manage');
  });

  test('source section shows path and layout', () => {
    const html = HP.makeDrawer(makePlugin({
      path: 'plugins/google_workspace.py',
      source_layout: 'flat',
    }));
    expect(html).toContain('plugins/google_workspace.py');
    expect(html).toContain('flat');
  });

  test('no secret value in drawer output (SAFETY)', () => {
    const html = HP.makeDrawer(makePlugin({
      credentials: [{ provider: 'google', source: 'keyring', hint: '****3f2a', env_var: null }]
    }));
    // hint is masked -- starts with ****; no raw credential value should appear
    expect(html).toContain('****3f2a');  // masked hint is fine
    // The raw value should not appear (no raw token in fixture anyway, but sanity check)
    expect(html).not.toContain('raw_secret_value');
  });
});

// ── makeErrorDrawer ───────────────────────────────────────────────────────────

describe('makeErrorDrawer', () => {
  test('returns empty for null', () => {
    expect(HP.makeErrorDrawer(null)).toBe('');
  });

  test('includes plugin_name and reason and detail', () => {
    const html = HP.makeErrorDrawer(makeError());
    expect(html).toContain('broken_thing');
    expect(html).toContain('REASON_NOT_INSPECTABLE_PATH');
    expect(html).toContain('subdir without server.py');
  });

  test('shows path in source section', () => {
    const html = HP.makeErrorDrawer(makeError({ path: 'plugins/broken_thing/' }));
    expect(html).toContain('plugins/broken_thing/');
  });

  test('has error status dot', () => {
    expect(HP.makeErrorDrawer(makeError())).toContain('hrns-dot-error');
  });

  test('no toggle rendered for error card (plugin not loaded)', () => {
    // S4 (#472) removed the S3 placeholder from error drawers -- a plugin
    // that failed to register has no orchestrator handle to enable/disable.
    const html = HP.makeErrorDrawer(makeError());
    expect(html).not.toContain('data-toggle-plugin');
    expect(html).not.toContain('hrns-toggle-placeholder');
  });
});

// ── filterRailHtml ────────────────────────────────────────────────────────────

describe('filterRailHtml', () => {
  var VOCAB = ['network', 'credentials_read', 'filesystem_read'];
  var PLUGINS = [
    makePlugin({ capabilities: ['network', 'credentials_read'] }),
    makePlugin({ name: 'other', capabilities: ['filesystem_read'] }),
  ];

  test('includes capabilities with >=1 plugin', () => {
    var html = HP.filterRailHtml(VOCAB, PLUGINS, [], makeFilters());
    expect(html).toContain('data-filter-cap="network"');
    expect(html).toContain('data-filter-cap="credentials_read"');
    expect(html).toContain('data-filter-cap="filesystem_read"');
  });

  test('omits capability not present in any plugin', () => {
    var vocab = ['network', 'exec'];
    var html = HP.filterRailHtml(vocab, PLUGINS, [], makeFilters());
    expect(html).not.toContain('data-filter-cap="exec"');
  });

  test('includes all 4 status filters', () => {
    var html = HP.filterRailHtml(VOCAB, PLUGINS, [], makeFilters());
    expect(html).toContain('data-filter-status="active"');
    expect(html).toContain('data-filter-status="error"');
    expect(html).toContain('data-filter-status="trusted_unverified"');
    expect(html).toContain('data-filter-status="disabled"');
  });

  test('active filter gets hrns-filter-active class', () => {
    var html = HP.filterRailHtml(VOCAB, PLUGINS, [], makeFilters({
      capabilities: ['network'],
      statuses: ['active'],
    }));
    expect(html).toContain('hrns-filter-active');
  });

  test('handles empty capVocab gracefully', () => {
    var html = HP.filterRailHtml([], PLUGINS, [], makeFilters());
    // No cap buttons, but status section still there.
    expect(html).toContain('data-filter-status="active"');
  });

  test('handles empty plugins gracefully', () => {
    var html = HP.filterRailHtml(VOCAB, [], [], makeFilters());
    // No caps have plugins, so no cap buttons rendered (shows "None").
    expect(html).not.toContain('data-filter-cap=');
  });
});

// ── applyFilters ──────────────────────────────────────────────────────────────

describe('applyFilters', () => {
  var PLUGINS = [
    makePlugin({ name: 'active_plugin',   status: 'active',   trust: 'inspected', capabilities: ['network'] }),
    makePlugin({ name: 'disabled_plugin', status: 'disabled', trust: 'inspected', capabilities: ['exec'] }),
    makePlugin({ name: 'trusted_plugin',  status: 'active',   trust: 'trusted',   capabilities: ['network'] }),
  ];
  var ERRORS = [makeError()];

  test('no filters returns all items', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters());
    expect(result.plugins).toHaveLength(3);
    expect(result.errors).toHaveLength(1);
  });

  test('status filter active returns only active plugins', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ statuses: ['active'] }));
    expect(result.plugins.every(function (p) { return p.status === 'active'; })).toBe(true);
  });

  test('status filter disabled returns only disabled plugins', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ statuses: ['disabled'] }));
    expect(result.plugins.every(function (p) { return p.status === 'disabled'; })).toBe(true);
  });

  test('trusted_unverified filter matches trust===trusted plugins', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ statuses: ['trusted_unverified'] }));
    expect(result.plugins.some(function (p) { return p.name === 'trusted_plugin'; })).toBe(true);
    expect(result.plugins.every(function (p) { return p.trust === 'trusted'; })).toBe(true);
  });

  test('error status filter includes error items', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ statuses: ['error'] }));
    expect(result.errors).toHaveLength(1);
  });

  test('non-error status filter hides error items', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ statuses: ['active'] }));
    expect(result.errors).toHaveLength(0);
  });

  test('cap filter returns plugins with that capability', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ capabilities: ['exec'] }));
    expect(result.plugins.some(function (p) { return p.name === 'disabled_plugin'; })).toBe(true);
    expect(result.plugins.some(function (p) { return p.name === 'active_plugin'; })).toBe(false);
  });

  test('multiple statuses union (OR)', () => {
    var result = HP.applyFilters(PLUGINS, ERRORS, makeFilters({ statuses: ['active', 'disabled'] }));
    expect(result.plugins).toHaveLength(3);  // all 3 match active or disabled
  });

  test('handles null inputs gracefully', () => {
    var result = HP.applyFilters(null, null, makeFilters());
    expect(result.plugins).toHaveLength(0);
    expect(result.errors).toHaveLength(0);
  });
});

// ── renderGrid ────────────────────────────────────────────────────────────────

describe('renderGrid', () => {
  test('renders cards for each plugin', () => {
    var html = HP.renderGrid([makePlugin()], [], makeFilters());
    expect(html).toContain('google_workspace');
  });

  test('renders error card for each refusal', () => {
    var html = HP.renderGrid([], [makeError()], makeFilters());
    expect(html).toContain('broken_thing');
    expect(html).toContain('hrns-card-error');
  });

  test('returns empty string when no plugins or errors', () => {
    expect(HP.renderGrid([], [], makeFilters())).toBe('');
  });

  test('filters applied before rendering', () => {
    var plugins = [
      makePlugin({ name: 'net_plugin', capabilities: ['network'] }),
      makePlugin({ name: 'exec_plugin', capabilities: ['exec'] }),
    ];
    var html = HP.renderGrid(plugins, [], makeFilters({ capabilities: ['network'] }));
    expect(html).toContain('net_plugin');
    expect(html).not.toContain('exec_plugin');
  });

  test('trusted_unverified badge always shown, never collapsed', () => {
    var plugin = makePlugin({ trust: 'trusted' });
    var html = HP.renderGrid([plugin], [], makeFilters());
    expect(html).toContain('trusted, unverified');
  });
});

// ── STATUS_FILTERS constant ───────────────────────────────────────────────────

test('STATUS_FILTERS has 4 entries', () => {
  expect(HP.STATUS_FILTERS).toHaveLength(4);
});

test('STATUS_FILTERS keys are active/error/trusted_unverified/disabled', () => {
  var keys = HP.STATUS_FILTERS.map(function (s) { return s.key; });
  expect(keys).toContain('active');
  expect(keys).toContain('error');
  expect(keys).toContain('trusted_unverified');
  expect(keys).toContain('disabled');
});

// ── S4 (#472): schemaToFormHtml + readSchemaForm + test-call block ──────────

describe('schemaToFormHtml (S4)', () => {
  test('object schema with string field renders labelled text input', () => {
    var schema = { type: 'object', properties: { to: { type: 'string' } } };
    var html = HP.schemaToFormHtml(schema, 'gmail_send');
    expect(html).toContain('hrns-test-form');
    expect(html).toContain('data-arg="to"');
    expect(html).toContain('data-type="string"');
    expect(html).toContain('type="text"');
  });

  test('number and integer fields render as type=number', () => {
    var schema = { type: 'object', properties: {
      count: { type: 'integer' }, ratio: { type: 'number' },
    } };
    var html = HP.schemaToFormHtml(schema, 't');
    expect(html).toContain('data-arg="count"');
    expect(html).toContain('data-arg="ratio"');
    expect((html.match(/\stype="number"/g) || []).length).toBe(2);
    expect(html).toContain('data-type="integer"');
    expect(html).toContain('data-type="number"');
  });

  test('boolean fields render as checkbox', () => {
    var schema = { type: 'object', properties: { silent: { type: 'boolean' } } };
    var html = HP.schemaToFormHtml(schema, 't');
    expect(html).toContain('type="checkbox"');
    expect(html).toContain('data-type="boolean"');
  });

  test('required fields marked with * indicator', () => {
    var schema = {
      type: 'object',
      properties: { to: { type: 'string' } },
      required: ['to'],
    };
    var html = HP.schemaToFormHtml(schema, 't');
    expect(html).toContain('hrns-test-req');
  });

  test('no properties -> empty label, no textarea, no fields', () => {
    var schema = { type: 'object', properties: {} };
    var html = HP.schemaToFormHtml(schema, 't');
    expect(html).toContain('hrns-test-empty');
    expect(html).not.toContain('hrns-test-json');
  });

  test('nested object schema falls back to JSON textarea', () => {
    var schema = { type: 'object', properties: {
      opts: { type: 'object' },  // nested object -> not simple
    } };
    var html = HP.schemaToFormHtml(schema, 't');
    expect(html).toContain('hrns-test-json');
    expect(html).toContain('data-mode="json"');
  });

  test('missing schema falls back to JSON textarea', () => {
    expect(HP.schemaToFormHtml(null, 't')).toContain('hrns-test-json');
    expect(HP.schemaToFormHtml(undefined, 't')).toContain('hrns-test-json');
    expect(HP.schemaToFormHtml({}, 't')).toContain('hrns-test-json');
  });

  test('array-typed field falls back to JSON textarea', () => {
    var schema = { type: 'object', properties: {
      tags: { type: 'array' },
    } };
    expect(HP.schemaToFormHtml(schema, 't')).toContain('hrns-test-json');
  });
});

// readSchemaForm needs DOM-shaped inputs. Roll a tiny fake element so we
// don't drag jsdom in when node's testEnvironment works everywhere else.
function fakeInput(attrs, value) {
  return {
    dataset: {
      arg:  attrs['data-arg'],
      type: attrs['data-type'],
      mode: attrs['data-mode'],
    },
    value:   value,
    checked: !!attrs.checked,
    getAttribute: function (k) { return attrs[k]; },
  };
}

function fakeRoot(fields, jsonEl) {
  return {
    querySelector: function (sel) {
      if (sel.indexOf('data-mode="json"') >= 0) return jsonEl || null;
      return null;
    },
    querySelectorAll: function () { return fields; },
  };
}

describe('readSchemaForm (S4)', () => {
  test('reads string and integer fields into args object', () => {
    var fields = [
      fakeInput({ 'data-arg': 'to',    'data-type': 'string'  }, 'a@b.com'),
      fakeInput({ 'data-arg': 'count', 'data-type': 'integer' }, '5'),
    ];
    var out = HP.readSchemaForm(fakeRoot(fields, null));
    expect(out).toEqual({ to: 'a@b.com', count: 5 });
  });

  test('boolean checkbox translates to bool', () => {
    var checked   = fakeInput({ 'data-arg': 'x', 'data-type': 'boolean', checked: true  }, '');
    var unchecked = fakeInput({ 'data-arg': 'y', 'data-type': 'boolean', checked: false }, '');
    var out = HP.readSchemaForm(fakeRoot([checked, unchecked], null));
    expect(out).toEqual({ x: true, y: false });
  });

  test('empty optional field is omitted', () => {
    var fields = [fakeInput({ 'data-arg': 'to', 'data-type': 'string' }, '')];
    var out = HP.readSchemaForm(fakeRoot(fields, null));
    expect(out).toEqual({});
  });

  test('JSON textarea parses valid JSON', () => {
    var jsonEl = { value: '{"a": 1, "b": "two"}' };
    var out = HP.readSchemaForm(fakeRoot([], jsonEl));
    expect(out).toEqual({ a: 1, b: 'two' });
  });

  test('empty JSON textarea returns empty object', () => {
    var jsonEl = { value: '' };
    expect(HP.readSchemaForm(fakeRoot([], jsonEl))).toEqual({});
  });

  test('malformed JSON returns error object, not exception', () => {
    var jsonEl = { value: '{not valid' };
    var out = HP.readSchemaForm(fakeRoot([], jsonEl));
    expect(out.__error).toMatch(/Invalid JSON/);
  });
});

describe('makeDrawer test-call section (S4)', () => {
  test('each tool gets a Test call button + args-form container', () => {
    var plugin = makePlugin({
      tools: [
        { name: 'gmail_send', description: 'Send email', supersedes: null,
          schema: { type: 'object', properties: { to: { type: 'string' } } } },
      ],
    });
    var html = HP.makeDrawer(plugin);
    expect(html).toContain('data-test-fire="gmail_send"');
    expect(html).toContain('data-test-tool="gmail_send"');
    expect(html).toContain('data-arg="to"');
    expect(html).toContain('Test call');
  });

  test('tool without schema falls back to JSON textarea', () => {
    var plugin = makePlugin({
      tools: [
        { name: 'no_schema_tool', description: 'x', supersedes: null, schema: null },
      ],
    });
    var html = HP.makeDrawer(plugin);
    expect(html).toContain('data-test-tool="no_schema_tool"');
    expect(html).toContain('hrns-test-json');
  });

  test('irreversible tool carries the flag on the test-call block', () => {
    var plugin = makePlugin({
      tools: [
        { name: 'gmail_send', description: 'Send', supersedes: null,
          irreversible: true, schema: null },
      ],
    });
    var html = HP.makeDrawer(plugin);
    expect(html).toContain('data-irreversible="true"');
  });

  test('result container present but hidden until response arrives', () => {
    var html = HP.makeDrawer(makePlugin());
    expect(html).toContain('data-test-result="gmail_send"');
    expect(html).toContain('hidden');
  });
});
