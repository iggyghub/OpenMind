'use strict';

const os   = require('os');
const path = require('path');
const fs   = require('fs');
const { SettingsStore } = require('../lib/settings-store');

let tmpFile;

beforeEach(() => {
  tmpFile = path.join(os.tmpdir(), `settings-test-${Date.now()}-${Math.random().toString(36).slice(2)}.json`);
});

afterEach(() => {
  try { fs.unlinkSync(tmpFile); } catch { /* already gone */ }
});

// ── Cycle 1: tracer bullet ────────────────────────────────────────────────────

test('returns default when file does not exist', () => {
  const store = new SettingsStore(tmpFile, { notifications_enabled: false });
  expect(store.get('notifications_enabled')).toBe(false);
});

// ── Cycle 2: corrupt file ─────────────────────────────────────────────────────

test('returns default when file is corrupt JSON', () => {
  fs.writeFileSync(tmpFile, 'not { valid json');
  const store = new SettingsStore(tmpFile, { notifications_enabled: false });
  expect(store.get('notifications_enabled')).toBe(false);
});

// ── Cycle 3: persistence ──────────────────────────────────────────────────────

test('set() writes value to disk immediately', () => {
  const store = new SettingsStore(tmpFile, {});
  store.set('notifications_enabled', true);
  const raw = JSON.parse(fs.readFileSync(tmpFile, 'utf-8'));
  expect(raw.notifications_enabled).toBe(true);
});

// ── Cycle 4: round-trip ───────────────────────────────────────────────────────

test('second instance reads value saved by first', () => {
  const s1 = new SettingsStore(tmpFile, {});
  s1.set('reminder_interval_minutes', 30);

  const s2 = new SettingsStore(tmpFile, { reminder_interval_minutes: 120 });
  expect(s2.get('reminder_interval_minutes')).toBe(30);
});

test('get returns default for missing key even after file exists', () => {
  const s1 = new SettingsStore(tmpFile, {});
  s1.set('other_key', 99);

  const s2 = new SettingsStore(tmpFile, { missing_key: 42 });
  expect(s2.get('missing_key')).toBe(42);
});
