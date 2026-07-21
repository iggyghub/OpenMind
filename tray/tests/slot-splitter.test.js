'use strict';

// Tests for tray/lib/slot-splitter.js (S3 -- #482).
// Covers clamp-to-minimum and persistence without a real pointer.
// DOM drag wiring is verified by the eye-only checklist in
// docs/harness-ui-live-verify.md.

const SlotSplitter = require('../lib/slot-splitter');

function makeMockStorage() {
  const store = {};
  return {
    getItem:    (k)    => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem:    (k, v) => { store[k] = String(v); },
    removeItem: (k)    => { delete store[k]; },
  };
}

beforeEach(() => { global.localStorage = makeMockStorage(); });
afterEach(()  => { delete global.localStorage; });

// ── clamp ─────────────────────────────────────────────────────────────────────

describe('clamp', () => {
  test('returns val when within range', () => {
    expect(SlotSplitter.clamp(300, 180, 800)).toBe(300);
  });

  test('clamps up to min when below minimum', () => {
    expect(SlotSplitter.clamp(50, 180, 800)).toBe(180);
  });

  test('clamps down to max when above maximum', () => {
    expect(SlotSplitter.clamp(900, 180, 800)).toBe(800);
  });

  test('when max < min, treats min as the effective max', () => {
    expect(SlotSplitter.clamp(100, 180, 100)).toBe(180);
  });

  test('exact min is returned as-is', () => {
    expect(SlotSplitter.clamp(180, 180, 800)).toBe(180);
  });

  test('exact max is returned as-is', () => {
    expect(SlotSplitter.clamp(800, 180, 800)).toBe(800);
  });
});

// ── readWidth / writeWidth ────────────────────────────────────────────────────

describe('readWidth / writeWidth', () => {
  test('returns DEFAULT_WIDTH when nothing is stored', () => {
    expect(SlotSplitter.readWidth()).toBe(SlotSplitter.DEFAULT_WIDTH);
  });

  test('round-trips a written width', () => {
    SlotSplitter.writeWidth(350);
    expect(SlotSplitter.readWidth()).toBe(350);
  });

  test('returns DEFAULT_WIDTH for a non-numeric stored value', () => {
    localStorage.setItem(SlotSplitter.STORAGE_KEY, 'bad');
    expect(SlotSplitter.readWidth()).toBe(SlotSplitter.DEFAULT_WIDTH);
  });

  test('overwrites a previous width', () => {
    SlotSplitter.writeWidth(400);
    SlotSplitter.writeWidth(250);
    expect(SlotSplitter.readWidth()).toBe(250);
  });
});

// ── computeWidth ──────────────────────────────────────────────────────────────
// Container: 1200px wide, right edge at x=1200; splitter 5px; minSec=180, minPri=200.

describe('computeWidth', () => {
  const right = 1200, width = 1200, spl = 5, minSec = 180, minPri = 200;

  test('mid-range drag returns raw delta', () => {
    // pointer at 700 -> secondary = 1200 - 700 = 500; within [180, 995]
    expect(SlotSplitter.computeWidth(700, right, width, spl, minSec, minPri)).toBe(500);
  });

  test('clamps secondary to minSecondary when pointer is near right edge', () => {
    // pointer at 1100 -> raw = 100, clamped to 180
    expect(SlotSplitter.computeWidth(1100, right, width, spl, minSec, minPri)).toBe(180);
  });

  test('clamps secondary to leave minPrimary when pointer is near left edge', () => {
    // pointer at 10 -> raw = 1190, max = 1200 - 5 - 200 = 995, clamped to 995
    expect(SlotSplitter.computeWidth(10, right, width, spl, minSec, minPri)).toBe(995);
  });

  test('exactly at min boundary stays at minSecondary', () => {
    // pointer at 1020 -> raw = 180, exactly at minSec
    expect(SlotSplitter.computeWidth(1020, right, width, spl, minSec, minPri)).toBe(180);
  });

  test('exactly at max boundary stays at max', () => {
    // pointer at 205 -> raw = 995, exactly at max
    expect(SlotSplitter.computeWidth(205, right, width, spl, minSec, minPri)).toBe(995);
  });
});
