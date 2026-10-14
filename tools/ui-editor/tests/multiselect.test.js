'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

// Mirror multi-select logic from inject.js (pure state, no DOM).
function makeState() {
  let selectedSet = new Set();
  const overrides = {};
  let snapshotCount = 0;
  let saveCount = 0;
  return {
    select(el) { selectedSet = new Set([el]); },
    addToSelection(el) { selectedSet.add(el); },
    clear() { selectedSet.clear(); },
    get size() { return selectedSet.size; },
    has(el) { return selectedSet.has(el); },
    primaryEl() { return selectedSet.size ? selectedSet.values().next().value : null; },
    patchOverride(id, patch) {
      const o = overrides[id] || (overrides[id] = {});
      if (patch.style) o.style = Object.assign(o.style || {}, patch.style);
    },
    setOverrideAll(ids, patch) {
      snapshotCount++;
      ids.forEach(id => this.patchOverride(id, patch));
      saveCount++;
    },
    get overrides() { return overrides; },
    get snapshotCount() { return snapshotCount; },
    get saveCount() { return saveCount; },
  };
}

test('select: sets single-element selection', () => {
  const s = makeState();
  s.select('a');
  assert.equal(s.size, 1);
  assert.ok(s.has('a'));
});

test('select: replaces existing selection', () => {
  const s = makeState();
  s.select('a');
  s.select('b');
  assert.equal(s.size, 1);
  assert.ok(!s.has('a'));
  assert.ok(s.has('b'));
});

test('addToSelection: adds without replacing', () => {
  const s = makeState();
  s.select('a');
  s.addToSelection('b');
  assert.equal(s.size, 2);
  assert.ok(s.has('a'));
  assert.ok(s.has('b'));
});

test('addToSelection: same element is a no-op (Set deduplication)', () => {
  const s = makeState();
  s.select('a');
  s.addToSelection('a');
  assert.equal(s.size, 1);
});

test('clear: empties selection (Escape behavior)', () => {
  const s = makeState();
  s.select('a');
  s.addToSelection('b');
  s.clear();
  assert.equal(s.size, 0);
});

test('primaryEl: returns first inserted element', () => {
  const s = makeState();
  s.select('a');
  s.addToSelection('b');
  assert.equal(s.primaryEl(), 'a');
});

test('primaryEl: returns null when empty', () => {
  const s = makeState();
  assert.equal(s.primaryEl(), null);
});

test('setOverrideAll: applies patch to every element', () => {
  const s = makeState();
  s.setOverrideAll(['a', 'b', 'c'], { style: { color: '#ff0000' } });
  assert.deepEqual(s.overrides['a'].style, { color: '#ff0000' });
  assert.deepEqual(s.overrides['b'].style, { color: '#ff0000' });
  assert.deepEqual(s.overrides['c'].style, { color: '#ff0000' });
});

test('setOverrideAll: takes exactly one snapshot and one save per call', () => {
  const s = makeState();
  s.setOverrideAll(['a', 'b', 'c'], { style: { color: '#ff0000' } });
  assert.equal(s.snapshotCount, 1);
  assert.equal(s.saveCount, 1);
});

test('setOverrideAll: merges styles, preserves other props', () => {
  const s = makeState();
  s.setOverrideAll(['a'], { style: { color: '#ff0000' } });
  s.setOverrideAll(['a'], { style: { backgroundColor: '#000000' } });
  assert.deepEqual(s.overrides['a'].style, { color: '#ff0000', backgroundColor: '#000000' });
});

test('shift-click sequence: plain click then shift-clicks accumulate', () => {
  const s = makeState();
  s.select('a');
  s.addToSelection('b');
  s.addToSelection('c');
  assert.equal(s.size, 3);
  assert.ok(s.has('a') && s.has('b') && s.has('c'));
});

test('plain click after multi-select collapses to single element', () => {
  const s = makeState();
  s.select('a');
  s.addToSelection('b');
  s.select('c');
  assert.equal(s.size, 1);
  assert.ok(s.has('c'));
  assert.ok(!s.has('a'));
  assert.ok(!s.has('b'));
});
