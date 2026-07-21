'use strict';

// Tests for tray/lib/sidebar-collapse.js (S1 -- #480).
// Covers toggle and localStorage persistence.

const SidebarCollapse = require('../lib/sidebar-collapse');

function makeMockStorage() {
  const store = {};
  return {
    getItem:    (k)    => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem:    (k, v) => { store[k] = String(v); },
    removeItem: (k)    => { delete store[k]; },
  };
}

function makeSidebar() {
  const classes = new Set();
  return {
    classList: {
      contains: (c) => classes.has(c),
      add:      (c) => classes.add(c),
      remove:   (c) => classes.delete(c),
    },
  };
}

beforeEach(() => { global.localStorage = makeMockStorage(); });
afterEach(() => { delete global.localStorage; });

describe('isCollapsed / setCollapsed', () => {
  test('defaults to false', () => {
    expect(SidebarCollapse.isCollapsed()).toBe(false);
  });

  test('persists collapsed=true', () => {
    SidebarCollapse.setCollapsed(true);
    expect(SidebarCollapse.isCollapsed()).toBe(true);
  });

  test('setCollapsed(false) removes the key', () => {
    SidebarCollapse.setCollapsed(true);
    SidebarCollapse.setCollapsed(false);
    expect(SidebarCollapse.isCollapsed()).toBe(false);
  });
});

describe('toggle', () => {
  test('adds is-collapsed and persists when sidebar is expanded', () => {
    const el = makeSidebar();
    const result = SidebarCollapse.toggle(el);
    expect(result).toBe(true);
    expect(el.classList.contains('is-collapsed')).toBe(true);
    expect(SidebarCollapse.isCollapsed()).toBe(true);
  });

  test('removes is-collapsed and persists when sidebar is collapsed', () => {
    const el = makeSidebar();
    SidebarCollapse.toggle(el);          // collapse
    const result = SidebarCollapse.toggle(el); // expand
    expect(result).toBe(false);
    expect(el.classList.contains('is-collapsed')).toBe(false);
    expect(SidebarCollapse.isCollapsed()).toBe(false);
  });
});
