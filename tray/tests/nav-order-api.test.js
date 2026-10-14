'use strict';

// Tests for the init() return API of tray/lib/nav-order.js (#1423):
// refresh(), draggingId(), endDrag().

const NavOrder = require('../lib/nav-order');

function mockStorage(initial) {
  const store = Object.assign({}, initial || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    _store: store,
  };
}

function makeEl(id) {
  const classes = new Set();
  return {
    id,
    dataset: {},
    attrs: {},
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    closest() { return this; },
    getBoundingClientRect: () => ({ top: 0, height: 10, bottom: 10 }),
  };
}

// A nav whose children can be added after init (like pinned Hub tiles).
function makeNav(ids) {
  let order = ids.map(makeEl);
  const listeners = {};
  return {
    add(id) { const el = makeEl(id); order.push(el); return el; },
    querySelectorAll: () => order.slice(),
    appendChild: (el) => { order = order.filter((x) => x !== el).concat([el]); },
    addEventListener: (t, fn) => { listeners[t] = fn; },
    fire: (t, e) => listeners[t](e),
    ids: () => order.map((e) => e.id),
    els: () => order.slice(),
  };
}

test('init returns null without a nav element', () => {
  expect(NavOrder.init(null, mockStorage())).toBeNull();
});

test('refresh applies the saved order to buttons added after init and marks them draggable', () => {
  const s = mockStorage({ [NavOrder.STORAGE_KEY]: JSON.stringify(['nav-pin-rea', 'b', 'a']) });
  const nav = makeNav(['a', 'b']);
  const api = NavOrder.init(nav, s);
  expect(nav.ids()).toEqual(['b', 'a']);
  const pin = nav.add('nav-pin-rea');
  api.refresh();
  expect(nav.ids()).toEqual(['nav-pin-rea', 'b', 'a']);
  expect(pin.attrs.draggable).toBe('true');
});

test('draggingId reports the dragged key and endDrag forgets it', () => {
  const nav = makeNav(['a', 'b']);
  const api = NavOrder.init(nav, mockStorage());
  expect(api.draggingId()).toBeNull();
  nav.fire('dragstart', { target: nav.els()[0], dataTransfer: { setData() {} } });
  expect(api.draggingId()).toBe('a');
  api.endDrag();
  expect(api.draggingId()).toBeNull();
});

test('startup apply does not rewrite storage (a saved slot for a not-yet-present pin survives)', () => {
  const saved = JSON.stringify(['nav-pin-rea', 'b', 'a']);
  const s = mockStorage({ [NavOrder.STORAGE_KEY]: saved });
  const nav = makeNav(['a', 'b']);
  const api = NavOrder.init(nav, s);
  expect(nav.ids()).toEqual(['b', 'a']);
  expect(s._store[NavOrder.STORAGE_KEY]).toBe(saved);
  api.refresh();
  expect(s._store[NavOrder.STORAGE_KEY]).toBe(saved);
});
