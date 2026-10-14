'use strict';

const os   = require('os');
const fs   = require('fs');
const path = require('path');
const { PositionStore, isPointOnAnyDisplay } = require('../lib/position-store');

function tmpPath() {
  return path.join(os.tmpdir(), `openmind-pos-test-${Date.now()}-${Math.random()}.json`);
}

afterEach(() => {
  // Clean up any temp files left by tests that saved
  jest.restoreAllMocks();
});

// ── Tracer bullet ─────────────────────────────────────────────────────────────

test('returns null when no file exists', () => {
  const store = new PositionStore(tmpPath());
  expect(store.load()).toBeNull();
});

// ── save / load round-trip ─────────────────────────────────────────────────────

test('saves and loads a position', () => {
  const file  = tmpPath();
  const store = new PositionStore(file);
  store.save({ x: 100, y: 200 });
  expect(store.load()).toEqual({ x: 100, y: 200 });
  fs.unlinkSync(file); // clean up
});

test('load returns null for corrupted file', () => {
  const file  = tmpPath();
  fs.writeFileSync(file, 'not json', 'utf8');
  const store = new PositionStore(file);
  expect(store.load()).toBeNull();
  fs.unlinkSync(file);
});

test('save does not throw when directory is unwritable', () => {
  const store = new PositionStore('/nonexistent/path/pos.json');
  expect(() => store.save({ x: 0, y: 0 })).not.toThrow();
});

test('overwrites previous position on repeated saves', () => {
  const file  = tmpPath();
  const store = new PositionStore(file);
  store.save({ x: 10, y: 20 });
  store.save({ x: 99, y: 88 });
  expect(store.load()).toEqual({ x: 99, y: 88 });
  fs.unlinkSync(file);
});

// ── isPointOnAnyDisplay -- #820 ─────────────────────────────────────────────

const _DISPLAY = { bounds: { x: 0, y: 0, width: 1920, height: 1080 } };
const _SECOND_DISPLAY = { bounds: { x: 1920, y: 0, width: 1280, height: 1024 } };

test('true for a point inside the only display', () => {
  expect(isPointOnAnyDisplay(500, 500, [_DISPLAY])).toBe(true);
});

test('false for a point past every display (e.g. monitor unplugged)', () => {
  expect(isPointOnAnyDisplay(3000, 3000, [_DISPLAY])).toBe(false);
});

test('true for a point on a second, non-primary display', () => {
  expect(isPointOnAnyDisplay(2500, 500, [_DISPLAY, _SECOND_DISPLAY])).toBe(true);
});

test('false for a negative-offset point when no display covers negative space', () => {
  expect(isPointOnAnyDisplay(-100, -100, [_DISPLAY])).toBe(false);
});

test('true for a point on the display\'s top-left corner (inclusive)', () => {
  expect(isPointOnAnyDisplay(0, 0, [_DISPLAY])).toBe(true);
});

test('false for a point exactly on the display\'s far edge (exclusive)', () => {
  expect(isPointOnAnyDisplay(1920, 1080, [_DISPLAY])).toBe(false);
});

test('false when displays list is empty', () => {
  expect(isPointOnAnyDisplay(500, 500, [])).toBe(false);
});
