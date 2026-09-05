'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

// Mirror the drop-targeting geometry from inject.js (pure logic, no DOM).
// Borrowed from Craft.js's Positioner (border-offset escape-to-parent) and
// GrapesJS's findPosition (before/after/append decision).
const CONTAINER_TAGS = ['DIV', 'SECTION', 'ARTICLE', 'ASIDE', 'MAIN', 'UL', 'OL', 'FORM', 'HEADER', 'FOOTER', 'NAV'];
const DROP_BORDER_OFFSET = 10;

function isNearEdge(rect, x, y, offset) {
  return (x - rect.left) < offset || (rect.right - x) < offset ||
    (y - rect.top) < offset || (rect.bottom - y) < offset;
}
function decideDropPosition(tag, childCount, rect, x, y) {
  if (childCount === 0 && CONTAINER_TAGS.indexOf(tag) !== -1) return 'append';
  return y < rect.top + rect.height / 2 ? 'before' : 'after';
}

// ---- isNearEdge ----

test('isNearEdge: point in the dead center is not near any edge', () => {
  const rect = { left: 0, right: 100, top: 0, bottom: 100 };
  assert.equal(isNearEdge(rect, 50, 50, DROP_BORDER_OFFSET), false);
});

test('isNearEdge: point within offset of the left edge', () => {
  const rect = { left: 0, right: 100, top: 0, bottom: 100 };
  assert.equal(isNearEdge(rect, 5, 50, DROP_BORDER_OFFSET), true);
});

test('isNearEdge: point within offset of each edge in turn', () => {
  const rect = { left: 0, right: 100, top: 0, bottom: 100 };
  assert.equal(isNearEdge(rect, 95, 50, DROP_BORDER_OFFSET), true); // right
  assert.equal(isNearEdge(rect, 50, 5, DROP_BORDER_OFFSET), true);  // top
  assert.equal(isNearEdge(rect, 50, 95, DROP_BORDER_OFFSET), true); // bottom
});

test('isNearEdge: exactly at the offset boundary is not near (strict <)', () => {
  const rect = { left: 0, right: 100, top: 0, bottom: 100 };
  assert.equal(isNearEdge(rect, 10, 50, DROP_BORDER_OFFSET), false);
});

// ---- decideDropPosition ----

test('decideDropPosition: empty container tag appends', () => {
  const rect = { top: 0, bottom: 100, height: 100 };
  assert.equal(decideDropPosition('DIV', 0, rect, 50, 50), 'append');
});

test('decideDropPosition: non-empty container falls through to before/after', () => {
  const rect = { top: 0, bottom: 100, height: 100 };
  assert.equal(decideDropPosition('DIV', 3, rect, 50, 10), 'before');
});

test('decideDropPosition: non-container tag never appends even when empty', () => {
  const rect = { top: 0, bottom: 100, height: 100 };
  assert.equal(decideDropPosition('SPAN', 0, rect, 50, 10), 'before');
});

test('decideDropPosition: point in top half of target is before', () => {
  const rect = { top: 0, bottom: 100, height: 100 };
  assert.equal(decideDropPosition('P', 0, rect, 50, 40), 'before');
});

test('decideDropPosition: point in bottom half of target is after', () => {
  const rect = { top: 0, bottom: 100, height: 100 };
  assert.equal(decideDropPosition('P', 0, rect, 50, 60), 'after');
});

test('decideDropPosition: exactly at vertical midpoint is after (>= not counted as before)', () => {
  const rect = { top: 0, bottom: 100, height: 100 };
  assert.equal(decideDropPosition('P', 0, rect, 50, 50), 'after');
});
