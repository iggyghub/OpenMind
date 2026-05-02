'use strict';

const os   = require('os');
const fs   = require('fs');
const path = require('path');
const { PositionStore } = require('../lib/position-store');

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
