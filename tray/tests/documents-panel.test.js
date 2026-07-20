'use strict';

// Unit tests for tray/lib/documents-panel.js -- S6 (issue #457).
// Pure data shapers: HTML building, date formatting, action payloads, and
// broadcast-driven list refresh modelled as two sequential renderList calls.

const DocumentsPanel = require('../lib/documents-panel');

// ── fixture helpers ──────────────────────────────────────────────────────────

function makeDoc(overrides) {
  return Object.assign({
    id:            1,
    name:          'My Resume',
    kind:          'docx',
    updated_at:    '2026-07-20T10:00:00Z',
    version_count: 2,
    versions:      ['v0_resume.docx', '20260720100000_resume.docx'],
  }, overrides || {});
}

// ── escHtml ──────────────────────────────────────────────────────────────────

describe('escHtml', () => {
  test('escapes & < > "', () => {
    expect(DocumentsPanel.escHtml('<b>&"test"</b>')).toBe('&lt;b&gt;&amp;&quot;test&quot;&lt;/b&gt;');
  });

  test('returns empty string for null/undefined', () => {
    expect(DocumentsPanel.escHtml(null)).toBe('');
    expect(DocumentsPanel.escHtml(undefined)).toBe('');
  });
});

// ── formatDate ───────────────────────────────────────────────────────────────

describe('formatDate', () => {
  test('returns empty string for missing input', () => {
    expect(DocumentsPanel.formatDate('')).toBe('');
    expect(DocumentsPanel.formatDate(null)).toBe('');
    expect(DocumentsPanel.formatDate(undefined)).toBe('');
  });

  test('parses a valid ISO timestamp and returns a non-empty string', () => {
    var result = DocumentsPanel.formatDate('2026-07-20T10:00:00Z');
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });

  test('falls back to the raw string on an unparseable value', () => {
    expect(DocumentsPanel.formatDate('not-a-date')).toBe('not-a-date');
  });
});

// ── makeRow ──────────────────────────────────────────────────────────────────

describe('makeRow', () => {
  test('returns empty string for null/undefined doc', () => {
    expect(DocumentsPanel.makeRow(null)).toBe('');
    expect(DocumentsPanel.makeRow(undefined)).toBe('');
  });

  test('includes doc name in output', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ name: 'Cover Letter' }));
    expect(html).toContain('Cover Letter');
  });

  test('includes kind badge', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ kind: 'pdf' }));
    expect(html).toContain('pdf');
    expect(html).toContain('docs-kind');
  });

  test('includes snapshot count', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ version_count: 3 }));
    expect(html).toContain('3 snapshots');
  });

  test('uses singular "snapshot" for count of 1', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ version_count: 1 }));
    expect(html).toContain('1 snapshot');
    expect(html).not.toContain('1 snapshots');
  });

  test('contains Open, Export, and Save buttons', () => {
    var html = DocumentsPanel.makeRow(makeDoc());
    expect(html).toContain('docs-open-btn');
    expect(html).toContain('docs-export-btn');
    expect(html).toContain('docs-save-btn');
  });

  test('contains format picker with FORMAT_OPTIONS', () => {
    var html = DocumentsPanel.makeRow(makeDoc());
    DocumentsPanel.FORMAT_OPTIONS.forEach(function (f) {
      expect(html).toContain(f.toUpperCase());
    });
  });

  test('contains versions toggle button when versions present', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ versions: ['v0_foo.docx'] }));
    expect(html).toContain('docs-versions-btn');
    expect(html).toContain('docs-versions-panel');
  });

  test('omits versions toggle when no versions', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ version_count: 0, versions: [] }));
    expect(html).not.toContain('docs-versions-btn');
    expect(html).not.toContain('docs-versions-panel');
  });

  test('each version row has a Revert button with data attributes', () => {
    var ver = 'v0_resume.docx';
    var html = DocumentsPanel.makeRow(makeDoc({ id: 7, versions: [ver] }));
    expect(html).toContain('docs-revert-btn');
    expect(html).toContain('data-doc-id="7"');
    expect(html).toContain('data-version="' + ver + '"');
  });

  test('sets data-doc-id on card root', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ id: 42 }));
    expect(html).toContain('data-doc-id="42"');
  });

  test('escapes HTML in name and kind', () => {
    var html = DocumentsPanel.makeRow(makeDoc({ name: '<script>', kind: '"evil"' }));
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&quot;evil&quot;');
  });
});

// ── renderList ───────────────────────────────────────────────────────────────

describe('renderList', () => {
  test('returns empty string for empty array', () => {
    expect(DocumentsPanel.renderList([])).toBe('');
  });

  test('returns empty string for non-array', () => {
    expect(DocumentsPanel.renderList(null)).toBe('');
    expect(DocumentsPanel.renderList(undefined)).toBe('');
  });

  test('renders one row for a single doc', () => {
    var html = DocumentsPanel.renderList([makeDoc()]);
    expect(html).toContain('My Resume');
  });

  test('renders multiple rows (broadcast-driven refresh simulation)', () => {
    var docs1 = [makeDoc({ id: 1, name: 'Resume' })];
    var docs2 = [
      makeDoc({ id: 1, name: 'Resume' }),
      makeDoc({ id: 2, name: 'Cover Letter', kind: 'docx' }),
    ];

    var html1 = DocumentsPanel.renderList(docs1);
    expect(html1).toContain('Resume');
    expect(html1).not.toContain('Cover Letter');

    // Simulate a documents_update broadcast arriving with more docs.
    var html2 = DocumentsPanel.renderList(docs2);
    expect(html2).toContain('Resume');
    expect(html2).toContain('Cover Letter');
  });
});

// ── actionFor ────────────────────────────────────────────────────────────────

describe('actionFor', () => {
  var doc = makeDoc({ id: 5 });

  test('open action targets doc_open with doc_id', () => {
    var action = DocumentsPanel.actionFor(doc, 'open');
    expect(action.type).toBe('call_tool');
    expect(action.data.name).toBe('doc_open');
    expect(action.data.args.doc_id).toBe(5);
  });

  test('export action targets doc_convert with format', () => {
    var action = DocumentsPanel.actionFor(doc, 'export', { format: 'pdf' });
    expect(action.type).toBe('call_tool');
    expect(action.data.name).toBe('doc_convert');
    expect(action.data.args.doc_id).toBe(5);
    expect(action.data.args.format).toBe('pdf');
  });

  test('export defaults to pdf when no format provided', () => {
    var action = DocumentsPanel.actionFor(doc, 'export', {});
    expect(action.data.args.format).toBe('pdf');
  });

  test('revert action targets doc_revert with version', () => {
    var action = DocumentsPanel.actionFor(doc, 'revert', { version: 'v0_resume.docx' });
    expect(action.type).toBe('call_tool');
    expect(action.data.name).toBe('doc_revert');
    expect(action.data.args.doc_id).toBe(5);
    expect(action.data.args.version).toBe('v0_resume.docx');
  });

  test('save_to_disk action sends doc_save_to_disk IPC type', () => {
    var action = DocumentsPanel.actionFor(doc, 'save_to_disk', { dest_path: 'C:\\Users\\user\\resume.docx' });
    expect(action.type).toBe('doc_save_to_disk');
    expect(action.data.doc_id).toBe(5);
    expect(action.data.dest_path).toBe('C:\\Users\\user\\resume.docx');
  });

  test('unknown action returns null', () => {
    expect(DocumentsPanel.actionFor(doc, 'nonexistent')).toBeNull();
  });
});

// ── FORMAT_OPTIONS ───────────────────────────────────────────────────────────

test('FORMAT_OPTIONS contains pdf, docx, txt, rtf', () => {
  expect(DocumentsPanel.FORMAT_OPTIONS).toContain('pdf');
  expect(DocumentsPanel.FORMAT_OPTIONS).toContain('docx');
  expect(DocumentsPanel.FORMAT_OPTIONS).toContain('txt');
  expect(DocumentsPanel.FORMAT_OPTIONS).toContain('rtf');
});
