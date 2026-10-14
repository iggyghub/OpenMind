'use strict';

// Tests for tray/lib/sidebar-resize.js.
// Covers clamp bounds and localStorage persistence (the pure helpers --
// pointer-drag wiring itself needs a real DOM/pointer, left to manual verify).

const SidebarResize = require('../lib/sidebar-resize');

function makeMockStorage() {
  const store = {};
  return {
    getItem:    (k)    => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem:    (k, v) => { store[k] = String(v); },
    removeItem: (k)    => { delete store[k]; },
  };
}

beforeEach(() => { global.localStorage = makeMockStorage(); });
afterEach(() => { delete global.localStorage; });

describe('clamp', () => {
  test('leaves an in-range value alone', () => {
    expect(SidebarResize.clamp(220)).toBe(220);
  });
  test('floors below MIN', () => {
    expect(SidebarResize.clamp(10)).toBe(SidebarResize.MIN);
  });
  test('ceils above MAX', () => {
    expect(SidebarResize.clamp(9999)).toBe(SidebarResize.MAX);
  });
});

describe('readWidth / writeWidth', () => {
  test('defaults when nothing stored', () => {
    expect(SidebarResize.readWidth()).toBe(SidebarResize.DEFAULT_WIDTH);
  });
  test('round-trips a written width', () => {
    SidebarResize.writeWidth(250);
    expect(SidebarResize.readWidth()).toBe(250);
  });
  test('clamps a corrupt/out-of-range stored value on read', () => {
    global.localStorage.setItem(SidebarResize.STORAGE_KEY, '99999');
    expect(SidebarResize.readWidth()).toBe(SidebarResize.MAX);
  });
  test('defaults on a non-numeric stored value', () => {
    global.localStorage.setItem(SidebarResize.STORAGE_KEY, 'not-a-number');
    expect(SidebarResize.readWidth()).toBe(SidebarResize.DEFAULT_WIDTH);
  });
});
