'use strict';

// Tests for tray/lib/nav-pins.js (#1423).

const NavPins = require('../lib/nav-pins');

function mockStorage(initial) {
  const store = Object.assign({}, initial || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    _store: store,
  };
}

test('pin appends once and persists; unpin removes', () => {
  const s = mockStorage();
  expect(NavPins.pin(s, 'rea')).toEqual(['rea']);
  expect(NavPins.pin(s, 'video')).toEqual(['rea', 'video']);
  expect(NavPins.pin(s, 'rea')).toEqual(['rea', 'video']);
  expect(NavPins.load(s)).toEqual(['rea', 'video']);
  expect(NavPins.unpin(s, 'rea')).toEqual(['video']);
  expect(NavPins.unpin(s, 'nope')).toEqual(['video']);
});

test('load tolerates bad JSON, wrong shape, junk entries, and null storage', () => {
  expect(NavPins.load(mockStorage({ [NavPins.STORAGE_KEY]: '{x' }))).toEqual([]);
  expect(NavPins.load(mockStorage({ [NavPins.STORAGE_KEY]: '{"a":1}' }))).toEqual([]);
  expect(NavPins.load(mockStorage({ [NavPins.STORAGE_KEY]: '["a",1,"","a","b"]' }))).toEqual(['a', 'b']);
  expect(NavPins.load(null)).toEqual([]);
  expect(() => NavPins.pin(null, 'a')).not.toThrow();
});

test('pin ignores empty / non-string ids', () => {
  const s = mockStorage();
  expect(NavPins.pin(s, '')).toEqual([]);
  expect(NavPins.pin(s, null)).toEqual([]);
});

test('visible keeps pinned order and skips plugins that are gone (without deleting)', () => {
  const s = mockStorage();
  NavPins.pin(s, 'video');
  NavPins.pin(s, 'gone');
  NavPins.pin(s, 'rea');
  const panels = [{ plugin_name: 'rea', title: 'REA' }, { plugin_name: 'video', title: 'Videos' }];
  expect(NavPins.visible(s, panels).map((p) => p.plugin_name)).toEqual(['video', 'rea']);
  expect(NavPins.load(s)).toEqual(['video', 'gone', 'rea']);
  expect(NavPins.visible(s, null)).toEqual([]);
});

test('elId / pluginFromElId round-trip; other ids are not pins', () => {
  expect(NavPins.elId('rea')).toBe('nav-pin-rea');
  expect(NavPins.pluginFromElId('nav-pin-rea')).toBe('rea');
  expect(NavPins.pluginFromElId('nav-ui-editor')).toBeNull();
  expect(NavPins.pluginFromElId('nav-pin-')).toBeNull();
  expect(NavPins.pluginFromElId(null)).toBeNull();
});
