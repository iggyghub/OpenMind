'use strict';

// Tests for tray/lib/nav-order.js (#1422).

const NavOrder = require('../lib/nav-order');

function mockStorage(initial) {
  const store = Object.assign({}, initial || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    _store: store,
  };
}

// Minimal fake nav: items are objects with dataset/id/classList; the nav
// records appendChild order and its listeners so tests can fire events.
function makeNav(ids) {
  const els = ids.map((id) => {
    const classes = new Set();
    return {
      id: '',
      dataset: { route: id },
      attrs: {},
      focused: false,
      classList: {
        add: (...c) => c.forEach((x) => classes.add(x)),
        remove: (...c) => c.forEach((x) => classes.delete(x)),
        has: (c) => classes.has(c),
      },
      setAttribute(k, v) { this.attrs[k] = v; },
      focus() { this.focused = true; },
      closest() { return this; },
      getBoundingClientRect: () => ({ top: 0, height: 10, bottom: 10 }),
    };
  });
  let order = els.slice();
  const listeners = {};
  return {
    els,
    querySelectorAll: () => order.slice(),
    appendChild: (el) => { order = order.filter((x) => x !== el).concat([el]); },
    addEventListener: (t, fn) => { listeners[t] = fn; },
    fire: (t, e) => listeners[t](e),
    ids: () => order.map((e) => e.dataset.route),
  };
}

describe('pure helpers', () => {
  test('applyOrder: saved first, unknown dropped, new ids appended', () => {
    expect(NavOrder.applyOrder(['a', 'b', 'c'], ['c', 'x', 'a'])).toEqual(['c', 'a', 'b']);
    expect(NavOrder.applyOrder(['a', 'b'], null)).toEqual(['a', 'b']);
    expect(NavOrder.applyOrder(['a', 'b'], ['b', 'b', 'a'])).toEqual(['b', 'a']);
  });

  test('move: before and after a target; unknown ids are a no-op', () => {
    expect(NavOrder.move(['a', 'b', 'c'], 'c', 'a', false)).toEqual(['c', 'a', 'b']);
    expect(NavOrder.move(['a', 'b', 'c'], 'a', 'c', true)).toEqual(['b', 'c', 'a']);
    expect(NavOrder.move(['a', 'b', 'c'], 'a', 'zz', true)).toEqual(['a', 'b', 'c']);
    expect(NavOrder.move(['a', 'b', 'c'], 'a', 'a', true)).toEqual(['a', 'b', 'c']);
  });

  test('step: moves by one and clamps at the ends', () => {
    expect(NavOrder.step(['a', 'b', 'c'], 'b', -1)).toEqual(['b', 'a', 'c']);
    expect(NavOrder.step(['a', 'b', 'c'], 'a', -1)).toEqual(['a', 'b', 'c']);
    expect(NavOrder.step(['a', 'b', 'c'], 'c', 1)).toEqual(['a', 'b', 'c']);
  });

  test('load/save round-trip; bad or missing storage yields []', () => {
    const s = mockStorage();
    expect(NavOrder.load(s)).toEqual([]);
    NavOrder.save(s, ['b', 'a']);
    expect(NavOrder.load(s)).toEqual(['b', 'a']);
    expect(NavOrder.load(mockStorage({ [NavOrder.STORAGE_KEY]: '{not json' }))).toEqual([]);
    expect(NavOrder.load(null)).toEqual([]);
    expect(() => NavOrder.save(null, ['a'])).not.toThrow();
  });
});

describe('init', () => {
  test('applies the saved order and marks items draggable', () => {
    const nav = makeNav(['a', 'b', 'c']);
    NavOrder.init(nav, mockStorage({ [NavOrder.STORAGE_KEY]: JSON.stringify(['c', 'a']) }));
    expect(nav.ids()).toEqual(['c', 'a', 'b']);
    expect(nav.els.every((e) => e.attrs.draggable === 'true')).toBe(true);
  });

  test('empty storage leaves the default order untouched and writes nothing', () => {
    const nav = makeNav(['a', 'b']);
    const s = mockStorage();
    NavOrder.init(nav, s);
    expect(nav.ids()).toEqual(['a', 'b']);
    expect(s._store[NavOrder.STORAGE_KEY]).toBeUndefined();
  });

  test('drag and drop reorders and persists', () => {
    const nav = makeNav(['a', 'b', 'c']);
    const s = mockStorage();
    NavOrder.init(nav, s);
    const [a, , c] = nav.els;
    nav.fire('dragstart', { target: c, dataTransfer: { setData() {} } });
    // drop on the top half of 'a' => before 'a'
    a.closest = () => a;
    nav.fire('drop', { target: a, clientY: 1, preventDefault() {} });
    expect(nav.ids()).toEqual(['c', 'a', 'b']);
    expect(JSON.parse(s._store[NavOrder.STORAGE_KEY])).toEqual(['c', 'a', 'b']);
  });

  test('Alt+ArrowDown moves the focused item down one', () => {
    const nav = makeNav(['a', 'b', 'c']);
    const s = mockStorage();
    NavOrder.init(nav, s);
    let prevented = false;
    nav.fire('keydown', {
      altKey: true, key: 'ArrowDown', target: nav.els[0],
      preventDefault() { prevented = true; },
    });
    expect(prevented).toBe(true);
    expect(nav.ids()).toEqual(['b', 'a', 'c']);
    expect(nav.els[0].focused).toBe(true);
  });

  test('plain arrows and other modifiers are ignored', () => {
    const nav = makeNav(['a', 'b']);
    NavOrder.init(nav, mockStorage());
    nav.fire('keydown', { altKey: false, key: 'ArrowDown', target: nav.els[0], preventDefault() {} });
    nav.fire('keydown', { altKey: true, ctrlKey: true, key: 'ArrowDown', target: nav.els[0], preventDefault() {} });
    expect(nav.ids()).toEqual(['a', 'b']);
  });
});

describe('drop on empty space beyond the list', () => {
  const emptySpace = { closest: () => null };

  test('dropping below the last button moves the item to the end', () => {
    const nav = makeNav(['a', 'b', 'c']);
    const s = mockStorage();
    NavOrder.init(nav, s);
    nav.fire('dragstart', { target: nav.els[0], dataTransfer: { setData() {} } });
    let prevented = false;
    nav.fire('dragover', { target: emptySpace, clientY: 500, preventDefault() { prevented = true; } });
    expect(prevented).toBe(true); // empty space is a valid drop target
    nav.fire('drop', { target: emptySpace, clientY: 500, preventDefault() {} });
    expect(nav.ids()).toEqual(['b', 'c', 'a']);
    expect(JSON.parse(s._store[NavOrder.STORAGE_KEY])).toEqual(['b', 'c', 'a']);
  });

  test('dropping above the first button moves the item to the start', () => {
    const nav = makeNav(['a', 'b', 'c']);
    NavOrder.init(nav, mockStorage());
    nav.fire('dragstart', { target: nav.els[2], dataTransfer: { setData() {} } });
    nav.fire('drop', { target: emptySpace, clientY: -5, preventDefault() {} });
    expect(nav.ids()).toEqual(['c', 'a', 'b']);
  });

  test('dragging the last item and dropping below it is a no-op', () => {
    const nav = makeNav(['a', 'b', 'c']);
    NavOrder.init(nav, mockStorage());
    nav.fire('dragstart', { target: nav.els[2], dataTransfer: { setData() {} } });
    let prevented = false;
    nav.fire('dragover', { target: emptySpace, clientY: 500, preventDefault() { prevented = true; } });
    expect(prevented).toBe(false);
    nav.fire('drop', { target: emptySpace, clientY: 500, preventDefault() {} });
    expect(nav.ids()).toEqual(['a', 'b', 'c']);
  });
});

