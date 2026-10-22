'use strict';

// Tests for tray/lib/ui-marks.js -- the pure helpers behind mark mode.
// The DOM wiring (init) is verified live in the Felix window.

const UiMarks = require('../lib/ui-marks');

// Minimal element fake: just what cssPath/describe touch.
function el(tag, { id = '', classes = [], parent = null, route = null, text = '' } = {}) {
  const node = {
    tagName: tag.toUpperCase(), id, textContent: text, parentElement: parent, children: [],
    classList: classes,
    getAttribute: (a) => (a === 'data-route' ? route : null),
    closest(sel) {
      for (let n = node; n; n = n.parentElement) if (n.getAttribute('data-route')) return n;
      return null;
    },
  };
  if (parent) parent.children.push(node);
  return node;
}

const computed = (map) => ({ getPropertyValue: (p) => map[p] || '' });

describe('cssPath', () => {
  test('stops at the nearest ancestor with an id', () => {
    const pane = el('section', { id: 'log-pane' });
    const row = el('div', { classes: ['log-row'], parent: pane });
    expect(UiMarks.cssPath(row)).toBe('#log-pane > div.log-row');
  });

  test('disambiguates same-tag siblings and drops its own uim- classes', () => {
    const list = el('ul', { id: 'l' });
    el('li', { parent: list });
    const second = el('li', { classes: ['item', 'uim-hover'], parent: list });
    expect(UiMarks.cssPath(second)).toBe('#l > li.item:nth-of-type(2)');
  });
});

describe('describe + format', () => {
  test('captures pane, trimmed text, non-default styles, and the note', () => {
    const pane = el('div', { id: 'p', route: 'log' });
    const line = el('span', { parent: pane, text: '  raw   JSON  blob ' });
    const mark = UiMarks.describe(line, computed({ 'font-size': '20px', padding: '0px', color: 'rgb(1, 2, 3)' }), 'too big');
    expect(mark).toEqual({
      pane: 'log', selector: '#p > span', text: 'raw JSON blob',
      styles: { 'font-size': '20px', color: 'rgb(1, 2, 3)' }, note: 'too big',
    });
    const out = UiMarks.format([mark]);
    expect(out).toContain('1. [log] too big');
    expect(out).toContain('selector: #p > span');
    expect(out).toContain('font-size: 20px; color: rgb(1, 2, 3)');
  });
});
