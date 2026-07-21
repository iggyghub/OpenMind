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
});

// ── renderWidget: security ───────────────────────────────────────────────────

describe('renderWidget security', () => {
  test('unknown widget type returns empty string (inert)', () => {
    expect(PanelSpec.renderWidget({ type: 'script', code: 'alert(1)' })).toBe('');
    expect(PanelSpec.renderWidget({ type: 'iframe', src: 'x' })).toBe('');
    expect(PanelSpec.renderWidget({ type: 'form' })).toBe('');   // A4/A5+ vocab, not yet
    expect(PanelSpec.renderWidget({ type: 'table' })).toBe('');
    expect(PanelSpec.renderWidget({ type: 'text' })).toBe('');
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
    expect(PanelSpec.WIDGET_TYPES.sort()).toEqual(['detail', 'list']);
  });
});
