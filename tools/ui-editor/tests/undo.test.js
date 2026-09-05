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
