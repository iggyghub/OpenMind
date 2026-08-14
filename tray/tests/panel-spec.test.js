'use strict';

// Tests for tray/lib/panel-spec.js -- UI2 A3 (#483).
// Pure data shapers: JSON spec -> HTML string. No DOM, no IPC.
//
// The security posture (SAFETY #3 in UI2.md, ADR-0012 decision 3):
//  - Unknown widget types are ignored, not injected.
//  - HTML-bearing plugin string values are escaped, never executed.

const PanelSpec = require('../lib/panel-spec');

// ── escHtml ──────────────────────────────────────────────────────────────────

describe('escHtml', () => {
  test('escapes & < > "', () => {
    expect(PanelSpec.escHtml('<b>&"x"</b>'))
      .toBe('&lt;b&gt;&amp;&quot;x&quot;&lt;/b&gt;');
  });

  test('returns empty string for null/undefined', () => {
    expect(PanelSpec.escHtml(null)).toBe('');
    expect(PanelSpec.escHtml(undefined)).toBe('');
  });
});

// ── renderWidget: list ───────────────────────────────────────────────────────

describe('renderWidget list', () => {
  test('renders items with title + subtitle', () => {
    const html = PanelSpec.renderWidget({
      type: 'list',
      items: [
        { title: 'Alpha', subtitle: 'txt' },
        { title: 'Beta' },
      ],
    });
    expect(html).toContain('ps-list');
    expect(html).toContain('Alpha');
    expect(html).toContain('Beta');
    expect(html).toContain('ps-list-sub');  // subtitle wrapper appears
    expect(html).toContain('txt');
  });

  test('empty items list renders inert empty-state marker', () => {
    const html = PanelSpec.renderWidget({ type: 'list', items: [] });
    expect(html).toContain('ps-empty');
    expect(html).not.toContain('<ul');
  });

  test('missing items array treated as empty', () => {
    const html = PanelSpec.renderWidget({ type: 'list' });
    expect(html).toContain('ps-empty');
  });
});

// ── renderWidget: detail ─────────────────────────────────────────────────────

describe('renderWidget detail', () => {
  test('renders label/value pairs', () => {
    const html = PanelSpec.renderWidget({
      type: 'detail',
      fields: [
        { label: 'Documents', value: '3' },
        { label: 'Library',   value: 'profile-scoped' },
      ],
    });
    expect(html).toContain('ps-detail');
    expect(html).toContain('Documents');
    expect(html).toContain('3');
    expect(html).toContain('profile-scoped');
  });

  test('empty fields list renders inert empty-state marker', () => {
    const html = PanelSpec.renderWidget({ type: 'detail', fields: [] });
    expect(html).toContain('ps-empty');
    expect(html).not.toContain('ps-detail-row');
  });

  test('field hint becomes an escaped native title tooltip', () => {
    const html = PanelSpec.renderWidget({
      type: 'detail',
      fields: [{ label: '', value: 'grill-me', hint: 'Grill a "plan" <hard>' }],
    });
    expect(html).toContain('title="Grill a &quot;plan&quot; &lt;hard&gt;"');
    expect(html).toContain('cursor:help');
    expect(html).toContain('grill-me');
  });

  test('field without hint has no title attribute', () => {
    const html = PanelSpec.renderWidget({
      type: 'detail',
      fields: [{ label: 'X', value: 'y' }],
    });
    expect(html).not.toContain('title=');
  });
});

// ── renderWidget: toggle ─────────────────────────────────────────────────────

describe('renderWidget toggle', () => {
  test('checked=true renders a checked switch carrying both tools', () => {
    const html = PanelSpec.renderWidget({
      type: 'toggle',
      id: 'skill-grill-me-toggle',
      label: 'Enabled',
      checked: true,
      enable_tool: 'skill_enable',
      disable_tool: 'skill_disable',
      tool_args: { name: 'grill-me' },
    });
    expect(html).toContain('ps-toggle');
    expect(html).toContain('type="checkbox" checked');
    expect(html).toContain('data-enable-tool="skill_enable"');
    expect(html).toContain('data-disable-tool="skill_disable"');
    expect(html).toContain('data-tool-args="{&quot;name&quot;:&quot;grill-me&quot;}"');
    expect(html).toContain('Enabled');
  });

  test('checked=false renders an unchecked switch', () => {
    const html = PanelSpec.renderWidget({
      type: 'toggle', enable_tool: 'skill_enable', disable_tool: 'skill_disable',
    });
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain('checked');
  });

  test('toggle is in the widget whitelist', () => {
    expect(PanelSpec.WIDGET_TYPES).toContain('toggle');
  });
});

// ── renderWidget: security ───────────────────────────────────────────────────

describe('renderWidget security', () => {
  test('unknown widget type returns empty string (inert)', () => {
    expect(PanelSpec.renderWidget({ type: 'script', code: 'alert(1)' })).toBe('');
    expect(PanelSpec.renderWidget({ type: 'iframe', src: 'x' })).toBe('');
    expect(PanelSpec.renderWidget({ type: 'form' })).toBe('');
  });

  test('malformed widget returns empty string', () => {
    expect(PanelSpec.renderWidget(null)).toBe('');
    expect(PanelSpec.renderWidget(undefined)).toBe('');
    expect(PanelSpec.renderWidget('string')).toBe('');
    expect(PanelSpec.renderWidget({})).toBe('');          // missing type
    expect(PanelSpec.renderWidget({ type: '' })).toBe('');
  });

  test('HTML-bearing string in a list item title is escaped, not executed', () => {
    const evil = '<script>alert("xss")</script>';
    const html = PanelSpec.renderWidget({
      type: 'list',
      items: [{ title: evil, subtitle: '<img src=x onerror=alert(1)>' }],
    });
    // The raw <script>/<img> tags never survive as executable markup --
    // escHtml neutralises the tag boundary itself, so anything that looks
    // like an event handler is trapped inside an inert text node.
    expect(html).not.toContain('<script');
    expect(html).not.toContain('<img');
    // The intended text content still shows up in escaped form.
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });

  test('HTML-bearing string in a detail value is escaped, not executed', () => {
    const evil = '<iframe src="javascript:alert(1)"></iframe>';
    const html = PanelSpec.renderWidget({
      type: 'detail',
      fields: [{ label: 'Bio', value: evil }],
    });
    expect(html).not.toContain('<iframe');
    expect(html).toContain('&lt;iframe');
    expect(html).toContain('javascript:alert(1)');  // present but as escaped text
  });

  test('HTML-bearing label is escaped, not executed', () => {
    const evilLabel = '<img src=x onerror=alert(1)>';
    const html = PanelSpec.renderWidget({
      type: 'detail',
      fields: [{ label: evilLabel, value: 'ok' }],
    });
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img');
  });

  test('quote-bearing string cannot break out of an attribute context', () => {
    // Not currently used in an attribute, but escHtml still escapes " so a
    // future widget that renders inside quotes stays safe.
    expect(PanelSpec.escHtml('"><script>alert(1)</script>'))
      .toBe('&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;');
  });
});

// ── renderWidget: text ───────────────────────────────────────────────────────

describe('renderWidget text', () => {
  test('renders a textarea with the widget value', () => {
    const html = PanelSpec.renderWidget({
      type: 'text', id: 'doc-text-1', value: 'hello world',
      tool: 'doc_write', tool_args: { doc_id: 1 },
    });
    expect(html).toContain('ps-text');
    expect(html).toContain('ps-text-area');
    expect(html).toContain('hello world');
    expect(html).toContain('ps-text-save');
    expect(html).toContain('ps-text-status');
  });

  test('renders label when provided', () => {
    const html = PanelSpec.renderWidget({
      type: 'text', id: 'x', label: 'notes.txt', value: '',
      tool: 'doc_write', tool_args: {},
    });
    expect(html).toContain('ps-text-label');
    expect(html).toContain('notes.txt');
  });

  test('omits label element when label is absent', () => {
    const html = PanelSpec.renderWidget({
      type: 'text', id: 'x', value: '', tool: 'doc_write', tool_args: {},
    });
    expect(html).not.toContain('ps-text-label');
  });

  test('embeds tool and tool_args in data attributes', () => {
    const html = PanelSpec.renderWidget({
      type: 'text', id: 'doc-text-2', value: '',
      tool: 'doc_write', tool_args: { doc_id: 2 },
    });
    expect(html).toContain('data-tool="doc_write"');
    expect(html).toContain('data-tool-args=');
    expect(html).toContain('doc_id');
  });

  test('HTML-bearing value is escaped, not executed', () => {
    const html = PanelSpec.renderWidget({
      type: 'text', id: 'x', value: '<script>alert(1)</script>',
      tool: 'doc_write', tool_args: {},
    });
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  test('null value renders empty textarea', () => {
    const html = PanelSpec.renderWidget({
      type: 'text', id: 'x', value: null, tool: 'doc_write', tool_args: {},
    });
    expect(html).toContain('<textarea class="ps-text-area"></textarea>');
  });
});

// ── renderWidget: action (S5 #542) ────────────────────────────────────────────

describe('renderWidget action', () => {
  test('renders a button with no input when input_arg is absent', () => {
    const html = PanelSpec.renderWidget({
      type: 'action', id: 'skill-x-uninstall', label: 'Uninstall',
      tool: 'skill_uninstall', tool_args: { name: 'x' },
    });
    expect(html).toContain('ps-action');
    expect(html).toContain('ps-action-btn');
    expect(html).toContain('Uninstall');
    expect(html).not.toContain('ps-action-input');
    expect(html).toContain('data-tool="skill_uninstall"');
    expect(html).toContain('name');
  });

  test('renders an input when input_arg is present', () => {
    const html = PanelSpec.renderWidget({
      type: 'action', id: 'skills-install', label: 'Install',
      tool: 'skill_install', tool_args: {},
      input_arg: 'repo', input_placeholder: 'owner/repo',
    });
    expect(html).toContain('ps-action-input');
    expect(html).toContain('data-input-arg="repo"');
    expect(html).toContain('placeholder="owner/repo"');
  });

  test('defaults label to "Run" when absent', () => {
    const html = PanelSpec.renderWidget({
      type: 'action', id: 'x', tool: 'skill_enable', tool_args: {},
    });
    expect(html).toContain('>Run<');
  });

  test('missing tool_args renders an empty object', () => {
    const html = PanelSpec.renderWidget({ type: 'action', id: 'x', tool: 'skill_enable' });
    expect(html).toContain('data-tool-args="{}"');
  });

  test('HTML-bearing label and placeholder are escaped, not executed', () => {
    const html = PanelSpec.renderWidget({
      type: 'action', id: 'x', tool: 'skill_enable',
      label: '<script>alert(1)</script>',
      input_arg: 'repo', input_placeholder: '<img src=x onerror=alert(1)>',
    });
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;img');
  });
});

// ── renderWidget: table (S4 #643) ─────────────────────────────────────────────

describe('renderWidget table', () => {
  test('renders columns and rows', () => {
    const html = PanelSpec.renderWidget({
      type: 'table',
      columns: ['Cluster', 'Count', 'Verdict'],
      rows: [
        ['dropshipping', '14', 'pending'],
        ['affiliate',    '8',  'pending'],
      ],
    });
    expect(html).toContain('ps-table');
    expect(html).toContain('ps-th');
    expect(html).toContain('ps-td');
    expect(html).toContain('Cluster');
    expect(html).toContain('dropshipping');
    expect(html).toContain('14');
  });

  test('empty columns and rows renders inert empty-state', () => {
    const html = PanelSpec.renderWidget({ type: 'table', columns: [], rows: [] });
    expect(html).toContain('ps-empty');
    expect(html).not.toContain('<table');
  });

  test('missing columns/rows treated as empty', () => {
    const html = PanelSpec.renderWidget({ type: 'table' });
    expect(html).toContain('ps-empty');
  });

  test('HTML in cells is escaped, not executed', () => {
    const html = PanelSpec.renderWidget({
      type: 'table',
      columns: ['<script>alert(1)</script>'],
      rows: [['<img src=x onerror=alert(1)>']],
    });
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;img');
  });

  test('null cell values render as empty string', () => {
    const html = PanelSpec.renderWidget({
      type: 'table',
      columns: ['A'],
      rows: [[null]],
    });
    expect(html).toContain('<td class="ps-td"></td>');
  });

  test('table is in the widget whitelist', () => {
    expect(PanelSpec.WIDGET_TYPES).toContain('table');
  });
});

// ── renderPanel ──────────────────────────────────────────────────────────────

describe('renderPanel', () => {
  test('renders title and widget tree', () => {
    const html = PanelSpec.renderPanel({
      title: 'Documents',
      widgets: [
        { type: 'list',   items:  [{ title: 'A' }] },
        { type: 'detail', fields: [{ label: 'Count', value: '1' }] },
      ],
    });
    expect(html).toContain('ps-panel');
    expect(html).toContain('ps-title');
    expect(html).toContain('Documents');
    expect(html).toContain('ps-list');
    expect(html).toContain('ps-detail');
  });

  test('null/undefined spec renders inert empty-state', () => {
    expect(PanelSpec.renderPanel(null)).toContain('ps-empty');
    expect(PanelSpec.renderPanel(undefined)).toContain('ps-empty');
  });

  test('malformed spec renders inert empty-state', () => {
    expect(PanelSpec.renderPanel('string')).toContain('ps-empty');
    expect(PanelSpec.renderPanel(42)).toContain('ps-empty');
  });

  test('spec title is escaped', () => {
    const html = PanelSpec.renderPanel({
      title: '<b>evil</b>',
      widgets: [],
    });
    expect(html).not.toContain('<b>evil</b>');
    expect(html).toContain('&lt;b&gt;evil&lt;/b&gt;');
  });

  test('unknown widgets are silently dropped, valid ones still render', () => {
    const html = PanelSpec.renderPanel({
      title: 'Mix',
      widgets: [
        { type: 'script', code: 'alert(1)' },   // ignored
        { type: 'list',   items: [{ title: 'kept' }] },
      ],
    });
    expect(html).not.toContain('alert');
    expect(html).not.toContain('<script>');
    expect(html).toContain('kept');
  });

  test('WIDGET_TYPES reports the whitelist', () => {
    expect(PanelSpec.WIDGET_TYPES.sort())
      .toEqual(['action', 'cluster', 'detail', 'group', 'list', 'table', 'text', 'toggle']);
  });
});

// ── renderWidget: group (native <details> collapsible) ───────────────────────

describe('renderWidget group', () => {
  test('renders a <details> with summary + escaped label and count', () => {
    const html = PanelSpec.renderWidget({
      type: 'group',
      label: 'Harness improvement',
      count: '3 clusters',
      widgets: [{ type: 'list', items: [{ title: 'inside' }] }],
    });
    expect(html).toContain('<details class="ps-group"');
    expect(html).toContain('Harness improvement');
    expect(html).toContain('3 clusters');
    expect(html).toContain('inside');            // child widget rendered
  });

  test('open:true adds the open attribute; falsy omits it', () => {
    expect(PanelSpec.renderWidget({ type: 'group', label: 'a', open: true }))
      .toContain('<details class="ps-group" open>');
    expect(PanelSpec.renderWidget({ type: 'group', label: 'a' }))
      .toContain('<details class="ps-group">');
  });

  test('escapes a malicious label and drops unknown child widgets', () => {
    const html = PanelSpec.renderWidget({
      type: 'group',
      label: '<img src=x onerror=alert(1)>',
      widgets: [{ type: 'script', code: 'alert(1)' }],
    });
    expect(html).not.toContain('<img');      // tag is escaped, never live markup
    expect(html).toContain('&lt;img');
  });

  test('collection tags the group as a drop target', () => {
    const html = PanelSpec.renderWidget({ type: 'group', label: 'a', collection: 'money-making idea' });
    expect(html).toContain('data-collection="money-making idea"');
  });
});

// ── renderWidget: cluster (draggable row + move-to select) ────────────────────

describe('renderWidget cluster', () => {
  test('renders a draggable row carrying id + move tool, with stats', () => {
    const html = PanelSpec.renderWidget({
      type: 'cluster', cluster_id: 42, label: 'Custom Harnesses',
      stats: '15 videos · skipped', collection: 'harness improvements',
      collections: ['harness improvements', 'money-making idea'],
      move_tool: 'video_move_cluster',
    });
    expect(html).toContain('draggable="true"');
    expect(html).toContain('data-cluster-id="42"');
    expect(html).toContain('data-move-tool="video_move_cluster"');
    expect(html).toContain('Custom Harnesses');
    expect(html).toContain('15 videos · skipped');
  });

  test('move-to options exclude the current collection', () => {
    const html = PanelSpec.renderWidget({
      type: 'cluster', cluster_id: 1, label: 'x', collection: 'A',
      collections: ['A', 'B'],
    });
    expect(html).toContain('<option value="B">B</option>');
    expect(html).not.toContain('<option value="A">A</option>');  // can't move to itself
  });

  test('escapes label and collection values', () => {
    const html = PanelSpec.renderWidget({
      type: 'cluster', cluster_id: 1, label: '<b>x</b>', collection: 'c',
      collections: ['"><img>'],
    });
    expect(html).not.toContain('<b>x</b>');
    expect(html).not.toContain('<img>');
    expect(html).toContain('&lt;b&gt;');
  });
});

// ── renderWidget: action checkbox field (#688 verify toggle) ─────────────────

describe('renderWidget action checkbox', () => {
  test('renders a checkbox carrying the arg name; checked reflects default', () => {
    const html = PanelSpec.renderWidget({
      type: 'action', id: 'video-batch-start', tool: 'video_batch_start',
      input_arg: 'url', input_arg2: 'category',
      checkbox_arg: 'verify', checkbox_label: 'Verify ideas', checkbox_checked: true,
    });
    expect(html).toContain('data-checkbox-arg="verify"');
    expect(html).toContain('ps-action-check');
    expect(html).toContain('type="checkbox" checked');
    expect(html).toContain('Verify ideas');
  });

  test('checkbox_checked falsy renders an unchecked box', () => {
    const html = PanelSpec.renderWidget({
      type: 'action', tool: 't', checkbox_arg: 'verify', checkbox_label: 'V',
    });
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain('checked');
  });

  test('no checkbox_arg -> no checkbox (backward-compatible)', () => {
    const html = PanelSpec.renderWidget({ type: 'action', tool: 't', input_arg: 'url' });
    expect(html).not.toContain('ps-action-check');
    expect(html).not.toContain('data-checkbox-arg');
  });
});
