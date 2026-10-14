'use strict';

// Tests for tray/lib/panel-prefs.js (Hub window preferences).

const PanelPrefs = require('../lib/panel-prefs');

function mockStorage(initial) {
  const store = Object.assign({}, initial || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    _store: store,
  };
}

describe('getPref / setPref', () => {
  test('defaults to inline', () => {
    expect(PanelPrefs.getPref(mockStorage(), 'rea')).toBe('inline');
  });

  test('round-trips window and back to inline', () => {
    const s = mockStorage();
    PanelPrefs.setPref(s, 'rea', 'window');
    expect(PanelPrefs.getPref(s, 'rea')).toBe('window');
    expect(PanelPrefs.getPref(s, 'video')).toBe('inline');
    PanelPrefs.setPref(s, 'rea', 'inline');
    expect(PanelPrefs.getPref(s, 'rea')).toBe('inline');
    expect(JSON.parse(s._store[PanelPrefs.STORAGE_KEY])).toEqual({});
  });

  test('bad JSON, wrong shape, or missing storage fall back to inline', () => {
    expect(PanelPrefs.getPref(mockStorage({ [PanelPrefs.STORAGE_KEY]: '{nope' }), 'a')).toBe('inline');
    expect(PanelPrefs.getPref(mockStorage({ [PanelPrefs.STORAGE_KEY]: '["a"]' }), 'a')).toBe('inline');
    expect(PanelPrefs.getPref(null, 'a')).toBe('inline');
    expect(() => PanelPrefs.setPref(null, 'a', 'window')).not.toThrow();
  });
});

describe('shouldDetach', () => {
  test('saved window preference detaches a plugin panel', () => {
    expect(PanelPrefs.shouldDetach({ pref: 'window', isBuiltin: false, width: 1600 })).toBe(true);
  });

  test('inline on a wide window stays in Felix', () => {
    expect(PanelPrefs.shouldDetach({ pref: 'inline', isBuiltin: false, width: 1600 })).toBe(false);
  });

  test('narrow window auto-detaches, boundary at NARROW_PX', () => {
    expect(PanelPrefs.shouldDetach({ pref: 'inline', isBuiltin: false, width: PanelPrefs.NARROW_PX - 1 })).toBe(true);
    expect(PanelPrefs.shouldDetach({ pref: 'inline', isBuiltin: false, width: PanelPrefs.NARROW_PX })).toBe(false);
  });

  test('built-in sections never detach', () => {
    expect(PanelPrefs.shouldDetach({ pref: 'window', isBuiltin: true, width: 300 })).toBe(false);
    expect(PanelPrefs.shouldDetach(null)).toBe(false);
  });
});

describe('filterPanels', () => {
  const panels = [
    { plugin_name: 'rea', title: 'REA' },
    { plugin_name: 'video', title: 'Videos' },
    { plugin_name: 'book_library', title: 'Books' },
  ];

  test('empty query returns everything (a copy)', () => {
    const out = PanelPrefs.filterPanels(panels, '  ');
    expect(out).toEqual(panels);
    expect(out).not.toBe(panels);
  });

  test('matches title or plugin name, case-insensitively', () => {
    expect(PanelPrefs.filterPanels(panels, 'vid').map((p) => p.plugin_name)).toEqual(['video']);
    expect(PanelPrefs.filterPanels(panels, 'BOOK').map((p) => p.plugin_name)).toEqual(['book_library']);
    expect(PanelPrefs.filterPanels(panels, 'zzz')).toEqual([]);
  });

  test('non-array input yields []', () => {
    expect(PanelPrefs.filterPanels(null, 'a')).toEqual([]);
  });
});
