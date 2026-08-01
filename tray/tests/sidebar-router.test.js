'use strict';

const { VALID_ROUTES, DEFAULT_ROUTE, HASH_REDIRECTS, routeFromHash, subFromHash } = require('../lib/sidebar-router');

// ── Constants ─────────────────────────────────────────────────────────────────

test('DEFAULT_ROUTE is conversation', () => {
  expect(DEFAULT_ROUTE).toBe('conversation');
});

test('VALID_ROUTES is a Set', () => {
  expect(VALID_ROUTES).toBeInstanceOf(Set);
});

// S5: 4 nav sections + profiles (kept accessible for first_run / header switcher)
test('VALID_ROUTES has 5 entries', () => {
  expect(VALID_ROUTES.size).toBe(5);
});

test('VALID_ROUTES contains the 4 nav sections and profiles', () => {
  const expected = ['conversation', 'harness', 'library', 'settings', 'profiles'];
  for (const route of expected) {
    expect(VALID_ROUTES.has(route)).toBe(true);
  }
});

test('DEFAULT_ROUTE is in VALID_ROUTES', () => {
  expect(VALID_ROUTES.has(DEFAULT_ROUTE)).toBe(true);
});

test('HASH_REDIRECTS is an object', () => {
  expect(typeof HASH_REDIRECTS).toBe('object');
  expect(HASH_REDIRECTS).not.toBeNull();
});

// ── routeFromHash — new valid routes resolve to themselves ────────────────────

describe('routeFromHash — new valid routes with # prefix', () => {
  test.each([...VALID_ROUTES])('#%s resolves to %s', (route) => {
    expect(routeFromHash('#' + route)).toBe(route);
  });
});

describe('routeFromHash — new valid routes without # prefix', () => {
  test.each([...VALID_ROUTES])('%s resolves to %s (no hash prefix)', (route) => {
    expect(routeFromHash(route)).toBe(route);
  });
});

// ── routeFromHash — sub-route strips correctly ────────────────────────────────

test('#harness/plugin_name resolves to harness', () => {
  expect(routeFromHash('#harness/my_plugin')).toBe('harness');
});

test('#library/memory resolves to library', () => {
  expect(routeFromHash('#library/memory')).toBe('library');
});

test('#settings/models resolves to settings', () => {
  expect(routeFromHash('#settings/models')).toBe('settings');
});

// ── routeFromHash — all 16 pre-collapse hashes redirect somewhere sensible ───

test('#plugins redirects to harness', () => {
  expect(routeFromHash('#plugins')).toBe('harness');
});

test('#integrations redirects to the Settings Sign-in tab', () => {
  expect(routeFromHash('#integrations')).toBe('settings');
  expect(subFromHash('#integrations')).toBe('signin');
});

test('#credentials redirects to the Settings Sign-in tab', () => {
  expect(routeFromHash('#credentials')).toBe('settings');
  expect(subFromHash('#credentials')).toBe('signin');
});

test('#permissions redirects to the Settings Permissions tab', () => {
  expect(routeFromHash('#permissions')).toBe('settings');
  expect(subFromHash('#permissions')).toBe('permissions');
});

test('#memory redirects to library', () => {
  expect(routeFromHash('#memory')).toBe('library');
});

test('#insights redirects to library', () => {
  expect(routeFromHash('#insights')).toBe('library');
});

test('#recipes redirects to library', () => {
  expect(routeFromHash('#recipes')).toBe('library');
});

test('#documents redirects to library', () => {
  expect(routeFromHash('#documents')).toBe('library');
});

test('#job-search redirects to library', () => {
  expect(routeFromHash('#job-search')).toBe('library');
});

test('#quick-ask redirects to conversation', () => {
  expect(routeFromHash('#quick-ask')).toBe('conversation');
});

test('#queue redirects to conversation', () => {
  expect(routeFromHash('#queue')).toBe('conversation');
});

test('#conversations redirects to conversation', () => {
  expect(routeFromHash('#conversations')).toBe('conversation');
});

test('#models redirects to settings', () => {
  expect(routeFromHash('#models')).toBe('settings');
});

// profiles stays in VALID_ROUTES (not redirected) for the first_run flow
test('#profiles resolves directly to profiles', () => {
  expect(routeFromHash('#profiles')).toBe('profiles');
});

// ── routeFromHash — fallback to DEFAULT_ROUTE ─────────────────────────────────

test('empty string falls back to DEFAULT_ROUTE', () => {
  expect(routeFromHash('')).toBe(DEFAULT_ROUTE);
});

test('undefined falls back to DEFAULT_ROUTE', () => {
  expect(routeFromHash(undefined)).toBe(DEFAULT_ROUTE);
});

test('null falls back to DEFAULT_ROUTE', () => {
  expect(routeFromHash(null)).toBe(DEFAULT_ROUTE);
});

test('unknown route falls back to DEFAULT_ROUTE', () => {
  expect(routeFromHash('#unknown')).toBe(DEFAULT_ROUTE);
});

test('bare # with no route falls back to DEFAULT_ROUTE', () => {
  expect(routeFromHash('#')).toBe(DEFAULT_ROUTE);
});

test('mixed-case route not in VALID_ROUTES falls back to DEFAULT_ROUTE', () => {
  expect(routeFromHash('#Queue')).toBe(DEFAULT_ROUTE);
});

// ── subFromHash ────────────────────────────────────────────────────────────────

test('subFromHash returns null for a plain valid route', () => {
  expect(subFromHash('#harness')).toBeNull();
  expect(subFromHash('#library')).toBeNull();
  expect(subFromHash('#settings')).toBeNull();
});

test('subFromHash returns the sub-part for #harness/plugin_name', () => {
  expect(subFromHash('#harness/my_plugin')).toBe('my_plugin');
});

test('subFromHash returns the sub-part for #library/memory', () => {
  expect(subFromHash('#library/memory')).toBe('memory');
});

test('subFromHash returns null for old redirect without sub', () => {
  expect(subFromHash('#plugins')).toBeNull();
  expect(subFromHash('#memory')).toBeNull();
});

test('#models redirects to the settings pane, models sub-tab', () => {
  expect(routeFromHash('#models')).toBe('settings');
  expect(subFromHash('#models')).toBe('models');
});

test('subFromHash returns null for empty/unknown', () => {
  expect(subFromHash('')).toBeNull();
  expect(subFromHash(undefined)).toBeNull();
  expect(subFromHash('#unknown')).toBeNull();
});
