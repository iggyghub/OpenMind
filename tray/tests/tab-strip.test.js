'use strict';

// Tests for tray/lib/tab-strip.js. No jsdom in this repo (see
// trading-panel.test.js's header) -- hand-rolled fakes, same convention as
// sidebar-collapse.test.js.

const TabStrip = require('../lib/tab-strip');

function makeMockStorage() {
  const store = {};
  return {
    getItem:    (k)    => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem:    (k, v) => { store[k] = String(v); },
    removeItem: (k)    => { delete store[k]; },
  };
}

// A fake tab button: classList set, dataset-free (identity is textContent).
function makeTab(label) {
  const classes = new Set();
  return {
    textContent: label,
    parentElement: null,
    classList: {
      contains: (c) => classes.has(c),
      add:      (c) => classes.add(c),
      remove:   (c) => classes.delete(c),
    },
  };
}

// A fake container holding an ordered list of tab elements, plus capturing
// addEventListener so tests can invoke the wired handlers directly.
function makeContainer(labels) {
  const kids = labels.map(makeTab);
  // compareDocumentPosition(other), called as `over.compareDocumentPosition(dragBtn)`:
  // resolves live off the shared kids array, so it stays correct across
  // in-test reorders (same convention real DOM order comparison gives free).
  kids.forEach((k) => {
    k.compareDocumentPosition = (other) => (kids.indexOf(other) > kids.indexOf(k) ? 4 : 2);
  });
  const handlers = {};
  const el = {
    scrollWidth: 500, clientWidth: 200, scrollLeft: 0,
    classList: { add() {}, remove() {} },
    addEventListener: (type, fn) => { handlers[type] = fn; },
    querySelectorAll: () => kids.slice(),
    appendChild: (kid) => {
      const i = kids.indexOf(kid);
      if (i !== -1) kids.splice(i, 1);
      kids.push(kid);
      kid.parentElement = el;
    },
    insertBefore: (kid, ref) => {
      const i = kids.indexOf(kid);
      if (i !== -1) kids.splice(i, 1);
      const j = ref ? kids.indexOf(ref) : kids.length;
      kids.splice(j === -1 ? kids.length : j, 0, kid);
      kid.parentElement = el;
    },
    contains: (node) => kids.includes(node),
  };
  kids.forEach((k) => { k.parentElement = el; });
  return { el, kids, handlers };
}

beforeEach(() => {
  global.localStorage = makeMockStorage();
  global.window = { addEventListener: () => {} };
  global.document = { elementFromPoint: () => null };
  global.Node = { DOCUMENT_POSITION_FOLLOWING: 4 };
});
afterEach(() => {
  delete global.localStorage;
  delete global.window;
  delete global.document;
  delete global.Node;
});

describe('init: wheel scroll', () => {
  test('down (positive deltaY) scrolls right when overflowing', () => {
    const { el, handlers } = makeContainer(['A', 'B']);
    TabStrip.init(el, { tabSelector: '.tab' });
    handlers.wheel({ deltaY: 40, preventDefault() {} });
    expect(el.scrollLeft).toBe(40);
  });

  test('no-ops when the strip does not overflow', () => {
    const { el, handlers } = makeContainer(['A']);
    el.scrollWidth = el.clientWidth;
    TabStrip.init(el, { tabSelector: '.tab' });
    handlers.wheel({ deltaY: 40, preventDefault() {} });
    expect(el.scrollLeft).toBe(0);
  });
});

describe('order persistence', () => {
  test('applies a saved order on init, ignoring unknown/missing entries', () => {
    localStorage.setItem('k', JSON.stringify(['C', 'A', 'ghost']));
    const { el, kids } = makeContainer(['A', 'B', 'C']);
    TabStrip.init(el, { tabSelector: '.tab', storageKey: 'k' });
    // C, A moved to front in that order; B (not in the saved list) stays put.
    expect(kids.map((k) => k.textContent)).toEqual(['B', 'C', 'A']);
  });

  test('a fresh strip with no saved order is left as-is', () => {
    const { el, kids } = makeContainer(['A', 'B']);
    TabStrip.init(el, { tabSelector: '.tab', storageKey: 'k2' });
    expect(kids.map((k) => k.textContent)).toEqual(['A', 'B']);
  });
});

describe('Shift+drag reorder', () => {
  test('dragging past a sibling swaps it, and the result is saved on drop', () => {
    const { el, kids, handlers } = makeContainer(['A', 'B', 'C']);
    const winHandlers = {};
    global.window.addEventListener = (type, fn) => { winHandlers[type] = fn; };
    TabStrip.init(el, { tabSelector: '.tab', storageKey: 'drag-key' });

    handlers.mousedown({ target: { closest: () => kids[0] }, shiftKey: true, preventDefault() {} });
    global.document.elementFromPoint = () => ({ closest: () => kids[2] });  // hover over C
    winHandlers.mousemove({ clientX: 10, clientY: 10 });
    winHandlers.mouseup();

    expect(kids.map((k) => k.textContent)).toEqual(['B', 'C', 'A']);
    expect(JSON.parse(localStorage.getItem('drag-key'))).toEqual(['B', 'C', 'A']);
  });

  test('a plain mousedown (no Shift) does not start a drag', () => {
    const { el, kids, handlers } = makeContainer(['A', 'B']);
    const winHandlers = {};
    global.window.addEventListener = (type, fn) => { winHandlers[type] = fn; };
    TabStrip.init(el, { tabSelector: '.tab', storageKey: 'no-drag' });

    handlers.mousedown({ target: { closest: () => kids[0] }, shiftKey: false, preventDefault() {} });
    global.document.elementFromPoint = () => ({ closest: () => kids[1] });
    winHandlers.mousemove({ clientX: 10, clientY: 10 });

    expect(kids.map((k) => k.textContent)).toEqual(['A', 'B']);  // unchanged
  });

  test('the click that follows a drag is swallowed before it reaches other handlers', () => {
    const { el, kids, handlers } = makeContainer(['A', 'B']);
    const winHandlers = {};
    global.window.addEventListener = (type, fn) => { winHandlers[type] = fn; };
    TabStrip.init(el, { tabSelector: '.tab', storageKey: 'click-swallow' });

    handlers.mousedown({ target: { closest: () => kids[0] }, shiftKey: true, preventDefault() {} });
    global.document.elementFromPoint = () => ({ closest: () => kids[1] });
    winHandlers.mousemove({ clientX: 10, clientY: 10 });
    winHandlers.mouseup();

    let stopped = false, prevented = false;
    winHandlers.click({
      target: kids[0],  // now sits wherever the drag left it, still inside el
      stopPropagation: () => { stopped = true; },
      preventDefault:  () => { prevented = true; },
    });
    expect(stopped).toBe(true);
    expect(prevented).toBe(true);
  });
});
