'use strict';

const { VALID_ROUTES, DEFAULT_ROUTE, routeFromHash } = require('../lib/sidebar-router');

// ── Constants ─────────────────────────────────────────────────────────────────

test('DEFAULT_ROUTE is conversation', () => {
  expect(DEFAULT_ROUTE).toBe('conversation');
});

test('VALID_ROUTES is a Set', () => {
  expect(VALID_ROUTES).toBeInstanceOf(Set);
});

test('VALID_ROUTES has 15 entries', () => {
  expect(VALID_ROUTES.size).toBe(15);
});

test('VALID_ROUTES contains all expected sidebar tabs', () => {
  const expected = [
    'conversation', 'quick-ask', 'queue', 'insights', 'memory',
    'permissions', 'credentials', 'plugins', 'profiles', 'settings', 'recipes',
    'models', 'conversations', 'integrations', 'job-search',
  ];
  for (const route of expected) {
    expect(VALID_ROUTES.has(route)).toBe(true);
  }
});

test('DEFAULT_ROUTE is in VALID_ROUTES', () => {
  expect(VALID_ROUTES.has(DEFAULT_ROUTE)).toBe(true);
});

// ── routeFromHash — valid inputs ──────────────────────────────────────────────

describe('routeFromHash — valid routes with # prefix', () => {
  test.each([...VALID_ROUTES])('#%s resolves to %s', (route) => {
    expect(routeFromHash('#' + route)).toBe(route);
  });
});

describe('routeFromHash — valid routes without # prefix', () => {
  test.each([...VALID_ROUTES])('%s resolves to %s (no hash prefix)', (route) => {
    expect(routeFromHash(route)).toBe(route);
  });
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
