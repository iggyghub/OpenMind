'use strict';

// Tests for tray/lib/attachment-chips.js (S14 -- #297).
//
// Pure data shaping + a tiny DOM helper. The DOM tests use the same
// node-html-parser the render-smoke harness uses so we don't pull in
// jsdom or electron just for chip building.

const AttachmentChips = require('../lib/attachment-chips');
const { parse } = require('node-html-parser');

// ── classify ────────────────────────────────────────────────────────────────

test('classify picks text on a known text suffix', () => {
  expect(AttachmentChips.classify('notes.md', 'application/octet-stream')).toBe('text');
});

test('classify picks pdf on .pdf suffix', () => {
  expect(AttachmentChips.classify('report.pdf', '')).toBe('pdf');
});

test('classify picks pdf on application/pdf mime', () => {
  expect(AttachmentChips.classify('blob.bin', 'application/pdf')).toBe('pdf');
});

test('classify picks image on image suffix (case insensitive)', () => {
  expect(AttachmentChips.classify('photo.JPG', '')).toBe('image');
});

test('classify picks image on image mime when suffix is unknown', () => {
  expect(AttachmentChips.classify('blob.bin', 'image/png')).toBe('image');
});

test('classify falls back to binary for unknown suffix + mime', () => {
  expect(AttachmentChips.classify('blob.dat', 'application/octet-stream')).toBe('binary');
});

test('classify falls back to binary on empty input', () => {
  expect(AttachmentChips.classify('', '')).toBe('binary');
});

// ── formatSize ──────────────────────────────────────────────────────────────

test('formatSize uses bytes under 1 KB', () => {
  expect(AttachmentChips.formatSize(0)).toBe('0 B');
  expect(AttachmentChips.formatSize(512)).toBe('512 B');
  expect(AttachmentChips.formatSize(1023)).toBe('1023 B');
});

test('formatSize uses KB between 1 KB and 1 MB', () => {
  expect(AttachmentChips.formatSize(2048)).toBe('2.0 KB');
  expect(AttachmentChips.formatSize(50 * 1024)).toBe('50.0 KB');
});

test('formatSize uses MB above 1 MB', () => {
  expect(AttachmentChips.formatSize(2 * 1024 * 1024)).toBe('2.0 MB');
});

test('formatSize handles non-numeric and negative as zero', () => {
  expect(AttachmentChips.formatSize('not a number')).toBe('0 B');
  expect(AttachmentChips.formatSize(-5)).toBe('0 B');
  expect(AttachmentChips.formatSize(NaN)).toBe('0 B');
});

// ── isAcceptable ────────────────────────────────────────────────────────────

test('isAcceptable rejects oversized files', () => {
  expect(AttachmentChips.isAcceptable({ size: AttachmentChips.MAX_INLINE_BYTES + 1 })).toBe(false);
});

test('isAcceptable accepts files at or below the cap', () => {
  expect(AttachmentChips.isAcceptable({ size: AttachmentChips.MAX_INLINE_BYTES })).toBe(true);
  expect(AttachmentChips.isAcceptable({ size: 1 })).toBe(true);
});

test('isAcceptable rejects malformed records', () => {
  expect(AttachmentChips.isAcceptable(null)).toBe(false);
  expect(AttachmentChips.isAcceptable({})).toBe(false);
});

// ── normalise ───────────────────────────────────────────────────────────────

test('normalise fills derived fields from a renderer-pending File record', () => {
  const rec = AttachmentChips.normalise({
    name: 'photo.png',
    type: 'image/png',
    size: 4096,
  });
  expect(rec.filename).toBe('photo.png');
  expect(rec.kind).toBe('image');
  expect(rec.size_label).toBe('4.0 KB');
  expect(rec.icon).toBe('▣');
  expect(rec.id).toBeNull();
  expect(rec.has_text).toBe(false);
});

test('normalise preserves backend-supplied id, kind, has_text', () => {
  const rec = AttachmentChips.normalise({
    id: 42,
    filename: 'notes.md',
    mime: 'text/markdown',
    size: 17,
    kind: 'text',
    has_text: true,
  });
  expect(rec.id).toBe(42);
  expect(rec.kind).toBe('text');
  expect(rec.has_text).toBe(true);
});

// ── buildChip / renderInto ─────────────────────────────────────────────────

function fakeDoc() {
  // Minimal document shim built on node-html-parser. The renderer code
  // uses createElement + appendChild + setAttribute + .className +
  // textContent + addEventListener, so the shim covers exactly that.
  // .className = 'x' must propagate to the class attribute so querySelector
  // can find the chip subparts -- the real browser does it; node-html-parser
  // doesn't, so we bridge it here.
  function el(tag) {
    const root = parse('<' + tag + '></' + tag + '>');
    const node = root.firstChild;
    node._listeners = {};
    node.ownerDocument = doc;
    node.addEventListener = function (ev, fn) {
      (node._listeners[ev] = node._listeners[ev] || []).push(fn);
    };
    Object.defineProperty(node, 'textContent', {
      configurable: true,
      get() { return this.text; },
      set(v) { this.set_content(v == null ? '' : String(v)); },
    });
    Object.defineProperty(node, 'className', {
      configurable: true,
      get() { return node.getAttribute('class') || ''; },
      set(v) { node.setAttribute('class', v == null ? '' : String(v)); },
    });
    Object.defineProperty(node, 'title', {
      configurable: true,
      get() { return node.getAttribute('title') || ''; },
      set(v) { node.setAttribute('title', v == null ? '' : String(v)); },
    });
    Object.defineProperty(node, 'type', {
      configurable: true,
      get() { return node.getAttribute('type') || ''; },
      set(v) { node.setAttribute('type', v == null ? '' : String(v)); },
    });
    return node;
  }
  const doc = {
    createElement: (t) => el(t),
  };
  return doc;
}

test('buildChip emits a span with name/meta and no remove button when no onRemove', () => {
  const doc = fakeDoc();
  const chip = AttachmentChips.buildChip(doc, {
    id: 7,
    filename: 'a.txt',
    mime: 'text/plain',
    size: 12,
    kind: 'text',
    has_text: true,
  });
  expect(chip.getAttribute('data-att-id')).toBe('7');
  expect(chip.getAttribute('data-att-kind')).toBe('text');
  const text = chip.text;
  expect(text).toContain('a.txt');
  expect(text).toContain('12 B');
  // No remove button (the .att-chip-x class) when no callback is given.
  expect(chip.querySelector('.att-chip-x')).toBeNull();
});

test('buildChip emits a remove button that fires onRemove(id) when clicked', () => {
  const doc = fakeDoc();
  let removed = null;
  const chip = AttachmentChips.buildChip(
    doc,
    { id: 11, filename: 'a.txt', size: 1, kind: 'text' },
    (id) => { removed = id; },
  );
  const rm = chip.querySelector('.att-chip-x');
  expect(rm).not.toBeNull();
  expect(rm.getAttribute('aria-label')).toBe('Remove attachment');
  // Invoke the captured click listener manually -- node-html-parser
  // doesn't dispatch events, so we read the listener back from the shim.
  expect(typeof rm._listeners.click[0]).toBe('function');
  rm._listeners.click[0]();
  expect(removed).toBe(11);
});

test('renderInto clears then renders chips for each record', () => {
  const doc = fakeDoc();
  const container = doc.createElement('div');
  // Pretend the container already has content -- renderInto must blow
  // it away first so a re-render doesn't duplicate chips.
  const stale = doc.createElement('span');
  stale.textContent = 'stale';
  container.appendChild(stale);

  const count = AttachmentChips.renderInto(container, [
    { id: 1, filename: 'a.txt', size: 1, kind: 'text' },
    { id: 2, filename: 'b.txt', size: 2, kind: 'text' },
  ]);
  expect(count).toBe(2);
  // No stale element survived the clear.
  expect(container.text.includes('stale')).toBe(false);
});

// ── summariseFilenames ─────────────────────────────────────────────────────

test('summariseFilenames joins filenames with comma-space', () => {
  const out = AttachmentChips.summariseFilenames([
    { filename: 'a.md' }, { filename: 'b.pdf' }, { filename: 'c.png' },
  ]);
  expect(out).toBe('a.md, b.pdf, c.png');
});

test('summariseFilenames is empty on empty input', () => {
  expect(AttachmentChips.summariseFilenames([])).toBe('');
  expect(AttachmentChips.summariseFilenames(null)).toBe('');
});
