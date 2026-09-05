'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

// Mirror elHint logic from inject.js (pure logic, no DOM).
function elHint(el) {
  for (var c = el.firstChild; c; c = c.nextSibling) {
    if (c.nodeType === 3) {
      var t = c.textContent.trim();
      if (t) return '"' + t.slice(0, 20) + (t.length > 20 ? '…' : '') + '"';
    }
  }
  if (el.id) return '#' + el.id;
  if (el.className && typeof el.className === 'string') {
    var cls = el.className.trim().split(/\s+/)[0];
    if (cls) return '.' + cls;
  }
  return '';
}

// Mirror tree walk cap logic from inject.js
const TREE_CAP = 200;
function walkCount(el, cap) {
  var count = 0;
  function walk(el) {
    if (count >= cap) return;
    count++;
    for (var i = 0; i < el.children.length; i++) walk(el.children[i]);
  }
  walk(el);
  return count;
}

// Minimal mock element builder
function makeEl(opts) {
  var textNodes = (opts.textNodes || []).map(function (t) {
    return { nodeType: 3, textContent: t };
  });
  for (var i = 0; i < textNodes.length - 1; i++) textNodes[i].nextSibling = textNodes[i + 1];
  if (textNodes.length) textNodes[textNodes.length - 1].nextSibling = null;
  return {
    id: opts.id || '',
    className: opts.className || '',
    firstChild: textNodes.length ? textNodes[0] : null,
    tagName: opts.tagName || 'DIV',
    children: opts.children || []
  };
}

// ---- elHint tests ----

test('elHint: returns quoted direct text node content', () => {
  const el = makeEl({ textNodes: ['Hello world'] });
  assert.equal(elHint(el), '"Hello world"');
});

test('elHint: truncates text over 20 chars with ellipsis', () => {
  const el = makeEl({ textNodes: ['This is a very long piece of text'] });
  const h = elHint(el);
  assert.match(h, /…/);
  assert.equal(h, '"This is a very long …"');
});

test('elHint: skips whitespace-only text nodes, falls through to id', () => {
  const el = makeEl({ textNodes: ['   '], id: 'myid' });
  assert.equal(elHint(el), '#myid');
});

test('elHint: falls back to id when no direct text', () => {
  const el = makeEl({ id: 'main' });
  assert.equal(elHint(el), '#main');
});

test('elHint: falls back to first class when no text or id', () => {
  const el = makeEl({ className: 'btn primary large' });
  assert.equal(elHint(el), '.btn');
});

test('elHint: returns empty string when no useful hint', () => {
  const el = makeEl({});
  assert.equal(elHint(el), '');
});

test('elHint: uses first non-empty text node among multiple', () => {
  const nodes = [
    { nodeType: 3, textContent: '  ' },
    { nodeType: 3, textContent: 'Click me' }
  ];
  nodes[0].nextSibling = nodes[1];
  nodes[1].nextSibling = null;
  const el = { id: '', className: '', firstChild: nodes[0], tagName: 'BUTTON', children: [] };
  assert.equal(elHint(el), '"Click me"');
});

// ---- tree walk cap tests ----

test('walkCount: counts all nodes in a flat list', () => {
  const children = [makeEl({}), makeEl({}), makeEl({})];
  const root = makeEl({ children });
  assert.equal(walkCount(root, TREE_CAP), 4); // root + 3
});

test('walkCount: counts nested nodes depth-first', () => {
  const grandchild = makeEl({ children: [] });
  const child = makeEl({ children: [grandchild] });
  const root = makeEl({ children: [child] });
  assert.equal(walkCount(root, TREE_CAP), 3);
});

test('walkCount: stops exactly at cap', () => {
  const children = Array.from({ length: 10 }, function () { return makeEl({}); });
  const root = makeEl({ children });
  assert.equal(walkCount(root, 5), 5);
});

test('walkCount: cap of 1 counts only root', () => {
  const root = makeEl({ children: [makeEl({}), makeEl({})] });
  assert.equal(walkCount(root, 1), 1);
});

test('walkCount: full tree within cap — no truncation', () => {
  const children = Array.from({ length: 3 }, function () { return makeEl({}); });
  const root = makeEl({ children });
  assert.equal(walkCount(root, 4), 4); // exactly 4, no cap hit
});
