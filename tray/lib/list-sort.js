'use strict';

// Shared sort helpers for the Library pane's tabs (Memory / Insights / Recipes /
// Documents) -- each renders its own item shape, but "sort by name" and "sort by
// date, newest/oldest first" is the same comparator logic every time. Extracted
// once instead of four near-identical inline .sort() calls.
//
// Dual-mode: window.ListSort in the renderer, module.exports for Node tests.

(function () {
  // Stable sort by a key-extractor function. Array#sort is spec-guaranteed
  // stable since ES2019 (every runtime this project targets), so ties keep
  // their original relative order rather than jumping around on every render.
  function sortBy(items, keyFn, direction) {
    var dir = direction === 'desc' ? -1 : 1;
    return items.slice().sort(function (a, b) {
      var ka = keyFn(a), kb = keyFn(b);
      if (ka < kb) return -1 * dir;
      if (ka > kb) return 1 * dir;
      return 0;
    });
  }

  // Case-insensitive string key, for alphabetical sorts.
  function alphaKey(s) { return String(s == null ? '' : s).toLowerCase(); }

  // Parses an ISO date string to epoch ms; a missing/invalid date sorts as 0
  // (oldest) rather than NaN, which would scatter it unpredictably through
  // the list instead of settling at one end.
  function dateKey(iso) {
    var t = iso ? Date.parse(iso) : NaN;
    return isNaN(t) ? 0 : t;
  }

  // Sorts by multiple keys in priority order -- e.g. pinned-first (a boolean
  // key, descending so `true` sorts before `false`) then newest-first within
  // each group. Each keyFns[i] pairs with directions[i] ('asc' default).
  function sortByMulti(items, keyFns, directions) {
    return items.slice().sort(function (a, b) {
      for (var i = 0; i < keyFns.length; i++) {
        var dir = (directions && directions[i] === 'desc') ? -1 : 1;
        var ka = keyFns[i](a), kb = keyFns[i](b);
        if (ka < kb) return -1 * dir;
        if (ka > kb) return 1 * dir;
      }
      return 0;
    });
  }

  var _exports = {
    sortBy: sortBy,
    sortByMulti: sortByMulti,
    alphaKey: alphaKey,
    dateKey: dateKey,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.ListSort = _exports;
  }
})();
