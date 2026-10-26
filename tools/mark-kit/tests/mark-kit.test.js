'use strict';

// Pure helpers behind mark-kit. The DOM wiring (init) is verified live in a host.
// Run: node --test "tools/mark-kit/tests/*.test.js"

const test = require('node:test');
const assert = require('node:assert');
const MarkKit = require('../mark-kit');

// Minimal element fake: just what cssPath/locate/resolve/describe touch.
// querySelectorAll understands '#id' and 'tag.class.class' only (what locate emits).
function el(tag, { id = '', classes = [], parent = null, text = '', role = null } = {}) {
  const node = {
    tagName: tag.toUpperCase(), id, parentElement: parent, children: [], classList: classes,
    get textContent() { return text + node.children.map((c) => c.textContent).join(''); },
    getAttribute: (a) => (a === 'role' ? role : null),
    querySelectorAll(sel) {
      const out = [];
      const walk = (n) => n.children.forEach((c) => { if (matches(c, sel)) out.push(c); walk(c); });
      walk(node);
      return out;
    },
    querySelector(sel) { return node.querySelectorAll(sel)[0] || null; },
  };
  if (parent) parent.children.push(node);
  return node;
}
function matches(n, sel) {
  if (sel[0] === '#') return n.id === sel.slice(1);
  const [tag, ...cls] = sel.split('.');
  return n.tagName.toLowerCase() === tag && cls.every((c) => n.classList.includes(c));
}
const computed = (map) => ({ getPropertyValue: (p) => map[p] || '' });

test('cssPath stops at the nearest id and drops uim-/is- classes', () => {
  const list = el('ul', { id: 'l' });
  el('li', { parent: list });
  const second = el('li', { classes: ['item', 'uim-hover', 'is-active'], parent: list });
  assert.strictEqual(MarkKit.cssPath(second), '#l > li.item:nth-of-type(2)');
});

test('locate: unique tag.class under the id anchor needs no index', () => {
  const doc = el('body');
  const pane = el('section', { id: 'log', parent: doc });
  const btn = el('button', { classes: ['del'], parent: el('div', { parent: pane }), text: 'x', role: 'button' });
  const loc = MarkKit.locate(btn);
  assert.deepStrictEqual(loc, { anchor: '#log', sel: 'button.del', text: 'x', role: 'button' });
  assert.strictEqual(MarkKit.resolve(doc, { locator: loc }), btn);
});

test('locate: text narrows; index only when text still ties', () => {
  const doc = el('body');
  const pane = el('div', { id: 'p', parent: doc });
  el('button', { classes: ['b'], parent: pane, text: 'Save' });
  const del1 = el('button', { classes: ['b'], parent: pane, text: 'Delete' });
  const del2 = el('button', { classes: ['b'], parent: pane, text: 'Delete' });
  assert.strictEqual(MarkKit.locate(del1).nth, 0);
  const loc2 = MarkKit.locate(del2);
  assert.strictEqual(loc2.nth, 1);
  assert.strictEqual(MarkKit.resolve(doc, { locator: loc2 }), del2);
});

test('resolve survives a re-render (new nodes, same shape) and returns null when gone', () => {
  const doc = el('body');
  const pane = el('div', { id: 'p', parent: doc });
  const loc = MarkKit.locate(el('span', { classes: ['row'], parent: pane, text: 'hello' }));
  pane.children.length = 0;  // re-render
  const fresh = el('span', { classes: ['row'], parent: pane, text: 'hello' });
  assert.strictEqual(MarkKit.resolve(doc, { locator: loc }), fresh);
  pane.children.length = 0;
  assert.strictEqual(MarkKit.resolve(doc, { locator: loc, selector: '#p > span.row' }), null);
});

test('describe + format carry note, locator, state and host context', () => {
  const pane = el('div', { id: 'p' });
  const line = el('span', { parent: pane, text: '  raw   JSON  blob ' });
  const mark = MarkKit.describe(line, computed({ 'font-size': '20px', padding: '0px' }), 'too big',
    { app: { route: 'log', subtab: '' }, viewport: { w: 800, h: 600 }, scroll: { x: 0, y: 40 }, errors: ['boom'] });
  assert.strictEqual(mark.text, 'raw JSON blob');
  assert.deepStrictEqual(mark.styles, { 'font-size': '20px' });
  assert.strictEqual(mark.selector, '#p > span');
  const out = MarkKit.format([mark]);
  assert.match(out, /1\. \[log\] too big/);
  assert.match(out, /locator: #p span "raw JSON blob"/);
  assert.match(out, /viewport: 800x600, scroll 0,40/);
  assert.match(out, /errors: boom/);
});

test('format still reads pre-mark-kit marks (pane, no locator)', () => {
  const out = MarkKit.format([{ pane: 'settings', note: 'n', selector: '#a', text: '', styles: {} }]);
  assert.match(out, /1\. \[settings\] n/);
});

test('likeSelector ignores state classes; format shows type, kind, scope and link', () => {
  assert.strictEqual(MarkKit.likeSelector(el('button', { classes: ['del', 'is-on', 'uim-hover'] })), 'button.del');
  const base = { selector: '#a', text: '', styles: {} };
  const out = MarkKit.format([
    { ...base, note: 'n1', type: 'fix' },
    { ...base, note: 'n2', type: 'remove', scope: 'all', match: 'button.del', matched: 7 },
    { ...base, note: 'n3', type: 'add', kind: 'section' },
    { ...base, note: 'n4', type: 'link', link: { relation: 'opens', locator: { anchor: '#p', sel: 'div.tile' }, selector: '#p > div.tile' } },
  ]);
  assert.match(out, /1\. \[\?\] n1\n/);
  assert.match(out, /\(Remove, all like this: 7 matched \(button\.del\)\) n2/);
  assert.match(out, /\(Add section\) n3/);
  assert.match(out, /opens: #p div\.tile/);
});

test('shotRect adds a margin and clamps to the viewport', () => {
  assert.deepStrictEqual(MarkKit.shotRect({ left: 10, top: 100, right: 60, bottom: 120 }, 800, 600, 40),
    { x: 0, y: 60, width: 100, height: 100 });
  assert.strictEqual(MarkKit.shotRect({ left: 900, top: 0, right: 950, bottom: 10 }, 800, 600, 0), null);
});

test('typed values never reach a mark: fields have no text', () => {
  const field = el('textarea', { text: 'my secret draft' });
  assert.strictEqual(MarkKit.describe(field, computed({}), 'n').text, '');
  assert.strictEqual(MarkKit.locate(el('input', { parent: el('div', { id: 'f' }) })).text, '');
});
