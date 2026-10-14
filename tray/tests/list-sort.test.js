'use strict';

// Tests for tray/lib/list-sort.js -- shared sort helpers for the Library
// pane's Memory/Insights/Recipes/Documents tabs.

const ListSort = require('../lib/list-sort');

describe('sortBy', () => {
  test('ascending by a numeric key', () => {
    const out = ListSort.sortBy([{ n: 3 }, { n: 1 }, { n: 2 }], (x) => x.n, 'asc');
    expect(out.map((x) => x.n)).toEqual([1, 2, 3]);
  });

  test('descending by a numeric key', () => {
    const out = ListSort.sortBy([{ n: 3 }, { n: 1 }, { n: 2 }], (x) => x.n, 'desc');
    expect(out.map((x) => x.n)).toEqual([3, 2, 1]);
  });

  test('does not mutate the input array', () => {
    const input = [{ n: 2 }, { n: 1 }];
    ListSort.sortBy(input, (x) => x.n, 'asc');
    expect(input.map((x) => x.n)).toEqual([2, 1]);
  });

  test('is stable -- ties keep original relative order', () => {
    const input = [{ n: 1, tag: 'a' }, { n: 1, tag: 'b' }, { n: 1, tag: 'c' }];
    const out = ListSort.sortBy(input, (x) => x.n, 'asc');
    expect(out.map((x) => x.tag)).toEqual(['a', 'b', 'c']);
  });
});

describe('alphaKey', () => {
  test('lowercases for case-insensitive comparison', () => {
    expect(ListSort.alphaKey('Banana') < ListSort.alphaKey('apple')).toBe(false);
    expect(ListSort.alphaKey('apple') < ListSort.alphaKey('Banana')).toBe(true);
  });
  test('treats null/undefined as empty string', () => {
    expect(ListSort.alphaKey(null)).toBe('');
    expect(ListSort.alphaKey(undefined)).toBe('');
  });
});

describe('dateKey', () => {
  test('orders older-before-newer', () => {
    expect(ListSort.dateKey('2024-01-01T00:00:00Z')).toBeLessThan(ListSort.dateKey('2025-01-01T00:00:00Z'));
  });
  test('missing or invalid date sorts as 0, not NaN', () => {
    expect(ListSort.dateKey(null)).toBe(0);
    expect(ListSort.dateKey('not-a-date')).toBe(0);
    expect(Number.isNaN(ListSort.dateKey('garbage'))).toBe(false);
  });
});

describe('sortByMulti', () => {
  test('pinned-first (desc boolean), then newest-first within each group', () => {
    const items = [
      { id: 1, pinned: false, at: '2024-01-01' },
      { id: 2, pinned: true,  at: '2024-01-01' },
      { id: 3, pinned: true,  at: '2024-06-01' },
      { id: 4, pinned: false, at: '2024-06-01' },
    ];
    const out = ListSort.sortByMulti(
      items,
      [(x) => x.pinned, (x) => ListSort.dateKey(x.at)],
      ['desc', 'desc']
    );
    expect(out.map((x) => x.id)).toEqual([3, 2, 4, 1]);
  });
});
