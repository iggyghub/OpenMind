'use strict';

// Unit tests for the persisted-state helpers in tray/lib/section-collapse.js
// (S3 — issue #286).  Covers storageKey format, setCollapsed, and isCollapsed.
// The init() function requires real browser DOM and is verified by the render-
// smoke harness + human live-verify checklist.

const SectionCollapse = require('../lib/section-collapse');

function makeMockStorage() {
  const store = {};
  return {
    getItem:    (k)    => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem:    (k, v) => { store[k] = String(v); },
    removeItem: (k)    => { delete store[k]; },
  };
}

beforeEach(() => {
  global.localStorage = makeMockStorage();
});

afterEach(() => {
  delete global.localStorage;
});

// ── storageKey ───────────────────────────────────────────────────────────────

describe('storageKey', () => {
  test('uses sec-collapse: prefix', () => {
    expect(SectionCollapse.storageKey('settings', 'Active model'))
      .toMatch(/^sec-collapse:/);
  });

  test('lowercases and slugifies the label', () => {
    expect(SectionCollapse.storageKey('settings', 'Active model'))
      .toBe('sec-collapse:settings:active-model');
  });

  test('collapses multiple spaces', () => {
    expect(SectionCollapse.storageKey('plugins', 'Registered  plugins'))
      .toBe('sec-collapse:plugins:registered-plugins');
  });

  test('trims surrounding whitespace', () => {
    expect(SectionCollapse.storageKey('permissions', '  Capability classes  '))
      .toBe('sec-collapse:permissions:capability-classes');
  });

  test('different paneKeys produce different keys', () => {
    const a = SectionCollapse.storageKey('settings', 'System');
    const b = SectionCollapse.storageKey('permissions', 'System');
    expect(a).not.toBe(b);
  });
});

// ── isCollapsed / setCollapsed ────────────────────────────────────────────────

describe('isCollapsed', () => {
  test('returns false when nothing is stored', () => {
    expect(SectionCollapse.isCollapsed('settings', 'Active model')).toBe(false);
  });
});

describe('setCollapsed', () => {
  test('persists collapsed=true', () => {
    SectionCollapse.setCollapsed('settings', 'Active model', true);
    expect(SectionCollapse.isCollapsed('settings', 'Active model')).toBe(true);
  });

  test('removes the key when collapsed=false', () => {
    SectionCollapse.setCollapsed('settings', 'Notifications', true);
    SectionCollapse.setCollapsed('settings', 'Notifications', false);
    expect(SectionCollapse.isCollapsed('settings', 'Notifications')).toBe(false);
  });

  test('different panes are independent', () => {
    SectionCollapse.setCollapsed('settings', 'System', true);
    expect(SectionCollapse.isCollapsed('plugins', 'System')).toBe(false);
  });

  test('different labels within the same pane are independent', () => {
    SectionCollapse.setCollapsed('settings', 'Notifications', true);
    expect(SectionCollapse.isCollapsed('settings', 'System')).toBe(false);
  });
});

// ── force-expand override (Settings > General > "Keep sections expanded") ────

describe('isForceExpand / setForceExpand', () => {
  test('defaults to false', () => {
    expect(SectionCollapse.isForceExpand()).toBe(false);
  });

  test('persists true', () => {
    SectionCollapse.setForceExpand(true);
    expect(SectionCollapse.isForceExpand()).toBe(true);
  });

  test('setForceExpand(false) removes the key', () => {
    SectionCollapse.setForceExpand(true);
    SectionCollapse.setForceExpand(false);
    expect(SectionCollapse.isForceExpand()).toBe(false);
  });
});

describe('isCollapsed with force-expand on', () => {
  test('overrides an explicitly collapsed section to false', () => {
    SectionCollapse.setCollapsed('settings', 'Active model', true);
    SectionCollapse.setForceExpand(true);
    expect(SectionCollapse.isCollapsed('settings', 'Active model')).toBe(false);
  });

  test('turning it back off restores the section\'s own remembered state', () => {
    SectionCollapse.setCollapsed('settings', 'Active model', true);
    SectionCollapse.setForceExpand(true);
    SectionCollapse.setForceExpand(false);
    expect(SectionCollapse.isCollapsed('settings', 'Active model')).toBe(true);
  });
});
