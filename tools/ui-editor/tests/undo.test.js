'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

// Mirror the undo/redo stack algorithm from inject.js (pure logic, no DOM).
// These tests verify the specification; inject.js implements the same contract.
const CAP = 50;
function makeStack() { return { undo: [], redo: [] }; }
function push(s, snap) {
  s.undo.push(JSON.parse(JSON.stringify(snap)));
  if (s.undo.length > CAP) s.undo.shift();
  s.redo = [];
}
function undo(s, current) {
  if (!s.undo.length) return null;
  s.redo.push(JSON.parse(JSON.stringify(current)));
  return s.undo.pop();
}
function redo(s, current) {
  if (!s.redo.length) return null;
  s.undo.push(JSON.parse(JSON.stringify(current)));
  return s.redo.pop();
}

test('push: adds snapshot to undo stack', () => {
  const s = makeStack();
  push(s, { a: 1 });
  assert.equal(s.undo.length, 1);
  assert.deepEqual(s.undo[0], { a: 1 });
});

test('push: stores deep copy, not reference', () => {
  const s = makeStack();
  const snap = { a: 1 };
  push(s, snap);
  snap.a = 99;
  assert.equal(s.undo[0].a, 1); // unaffected by later mutation
});

test('push: clears redo stack', () => {
  const s = makeStack();
  push(s, { a: 1 });
  s.redo.push({ x: 9 });
  push(s, { a: 2 });
  assert.equal(s.redo.length, 0);
});

test('undo: returns previous snapshot', () => {
  const s = makeStack();
  push(s, { a: 1 });
  const result = undo(s, { a: 2 });
  assert.deepEqual(result, { a: 1 });
});

test('undo: pushes current state onto redo stack', () => {
  const s = makeStack();
  push(s, { a: 1 });
  undo(s, { a: 2 });
  assert.deepEqual(s.redo, [{ a: 2 }]);
});

test('undo: returns null when stack is empty', () => {
  const s = makeStack();
  assert.equal(undo(s, { a: 1 }), null);
});

test('redo: returns forward snapshot', () => {
  const s = makeStack();
  push(s, { a: 1 });
  const prev = undo(s, { a: 2 });  // prev = { a: 1 }, redo has { a: 2 }
  const result = redo(s, prev);
  assert.deepEqual(result, { a: 2 });
});

test('redo: pushes current state back onto undo stack', () => {
  const s = makeStack();
  push(s, { a: 1 });
  const prev = undo(s, { a: 2 });
  redo(s, prev);
  assert.equal(s.undo.length, 1);
  assert.deepEqual(s.undo[0], { a: 1 });
});

test('redo: returns null when stack is empty', () => {
  const s = makeStack();
  assert.equal(redo(s, {}), null);
});

test('cap: oldest entry dropped when limit exceeded', () => {
  const s = makeStack();
  for (let i = 0; i < CAP + 10; i++) push(s, { n: i });
  assert.equal(s.undo.length, CAP);
  assert.deepEqual(s.undo[0], { n: 10 }); // first 10 evicted
});

test('cap: exactly CAP entries fit without eviction', () => {
  const s = makeStack();
  for (let i = 0; i < CAP; i++) push(s, { n: i });
  assert.equal(s.undo.length, CAP);
  assert.deepEqual(s.undo[0], { n: 0 }); // nothing evicted
});

test('multi-step undo/redo cycle', () => {
  const s = makeStack();
  push(s, { v: 'a' });
  push(s, { v: 'b' });
  // current state is { v: 'c' }
  const b = undo(s, { v: 'c' }); assert.deepEqual(b, { v: 'b' });
  const a = undo(s, b);          assert.deepEqual(a, { v: 'a' });
  assert.equal(undo(s, a), null); // nothing left
  const b2 = redo(s, a);         assert.deepEqual(b2, { v: 'b' });
  const c2 = redo(s, b2);        assert.deepEqual(c2, { v: 'c' });
  assert.equal(redo(s, c2), null);
});

// ---- throttled coalescing (borrowed from Craft.js history throttling) ----
// A burst of edits closer together than THROTTLE_MS shouldn't each get their own undo
// step -- only a pause longer than that starts a new one. Mirrors inject.js's snapshot().
const THROTTLE_MS = 500;
function shouldCoalesce(stackLength, now, lastSnapshotAt, throttleMs) {
  return stackLength > 0 && (now - lastSnapshotAt) < throttleMs;
}
function pushThrottled(s, snap, now, state) {
  if (shouldCoalesce(s.undo.length, now, state.lastSnapshotAt, THROTTLE_MS)) {
    state.lastSnapshotAt = now;
    s.redo = [];
    return false; // coalesced, no new entry
  }
  state.lastSnapshotAt = now;
  push(s, snap);
  return true; // new entry pushed
}

test('throttle: first edit always pushes (empty stack, nothing to coalesce into)', () => {
  const s = makeStack();
  const state = { lastSnapshotAt: 0 };
  assert.equal(pushThrottled(s, { a: 1 }, 1000, state), true);
  assert.equal(s.undo.length, 1);
});

test('throttle: a second edit within the window coalesces (no new entry)', () => {
  const s = makeStack();
  const state = { lastSnapshotAt: 0 };
  pushThrottled(s, { a: 1 }, 1000, state);
  const pushed = pushThrottled(s, { a: 2 }, 1000 + 100, state);
  assert.equal(pushed, false);
  assert.equal(s.undo.length, 1); // still just the one entry from the burst start
});

test('throttle: an edit after the window elapses starts a new step', () => {
  const s = makeStack();
  const state = { lastSnapshotAt: 0 };
  pushThrottled(s, { a: 1 }, 1000, state);
  const pushed = pushThrottled(s, { a: 2 }, 1000 + THROTTLE_MS + 1, state);
  assert.equal(pushed, true);
  assert.equal(s.undo.length, 2);
});

test('throttle: window is rolling -- each edit in a burst extends it, not just the first', () => {
  const s = makeStack();
  const state = { lastSnapshotAt: 0 };
  pushThrottled(s, { a: 1 }, 1000, state);           // starts burst
  pushThrottled(s, { a: 2 }, 1000 + 400, state);      // within window, extends it
  pushThrottled(s, { a: 3 }, 1000 + 400 + 400, state); // 400ms after the LAST edit, still within window
  assert.equal(s.undo.length, 1); // whole burst stayed one step
});

test('throttle: coalescing still clears the redo stack (a mid-burst edit invalidates redo)', () => {
  const s = makeStack();
  const state = { lastSnapshotAt: 0 };
  pushThrottled(s, { a: 1 }, 1000, state);
  s.redo.push({ stale: true });
  pushThrottled(s, { a: 2 }, 1000 + 100, state);
  assert.equal(s.redo.length, 0);
});
