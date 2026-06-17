'use strict';

// Federated search registry (S4 -- issue #287).
//
// Each pane registers a provider: (query) => [{ label, route, anchor, secondary? }].
// The shell calls search(query, currentRoute) and gets back:
//   { current:   hits from the active pane's provider,
//     elsewhere: ranked hits from every other pane's provider }.
//
// Ranking is rank-based, not similarity-based: exact label match beats prefix
// match beats word-start match beats substring; shorter labels break ties.
//
// Dual-mode export: same source feeds the Node test suite via require() AND
// the Main window renderer via window.SearchRegistry. Body wrapped in an IIFE
// so a plain <script src> tag doesn't collide on top-level consts.
//
// SECURITY: the registry does not inspect provider output. The Credentials
// provider is the load-bearing place that must return only labels/status and
// never secret values. Tests in this module assert the contract by feeding a
// fake credentials provider and asserting no secret leaks into the results.

(function () {
  var EXACT      = 0;
  var PREFIX     = 1;
  var WORD_START = 2;
  var SUBSTRING  = 3;
  var NO_MATCH   = 4;

  function _norm(s) {
    return (s == null ? '' : String(s)).toLowerCase().trim();
  }

  // Returns one of EXACT/PREFIX/WORD_START/SUBSTRING/NO_MATCH for label vs query.
  function _matchClass(query, label) {
    var q = _norm(query);
    var l = _norm(label);
    if (!q) return NO_MATCH;
    if (l === q) return EXACT;
    if (l.indexOf(q) === 0) return PREFIX;
    // Word-start: query occurs immediately after whitespace, '-', '_', '.', or '/'.
    var wordStart = new RegExp('(?:^|[\\s\\-_./])' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    if (wordStart.test(l)) return WORD_START;
    if (l.indexOf(q) !== -1) return SUBSTRING;
    return NO_MATCH;
  }

  // Score a hit lower-is-better: primary by match class, then label length.
  function score(query, hit) {
    var cls = _matchClass(query, hit.label);
    if (cls === NO_MATCH && hit.secondary) cls = _matchClass(query, hit.secondary);
    if (cls === NO_MATCH) return Infinity;
    var len = _norm(hit.label).length;
    // class dominates by * 10000; length adds [0, 9999].
    return cls * 10000 + Math.min(len, 9999);
  }

  function _hitMatches(query, hit) {
    if (_matchClass(query, hit.label) !== NO_MATCH) return true;
    if (hit.secondary && _matchClass(query, hit.secondary) !== NO_MATCH) return true;
    return false;
  }

  function _rankHits(query, hits) {
    return hits
      .filter(function (h) { return _hitMatches(query, h); })
      .map(function (h) { return { hit: h, score: score(query, h) }; })
      .sort(function (a, b) { return a.score - b.score; })
      .map(function (x) { return x.hit; });
  }

  // Create an isolated registry. The shell uses the default singleton, but
  // tests use their own to avoid cross-test pollution.
  function create() {
    var providers = new Map();

    function register(route, provider) {
      if (!route || typeof route !== 'string') {
        throw new Error('search-registry.register: route required');
      }
      if (typeof provider !== 'function') {
        throw new Error('search-registry.register: provider must be a function');
      }
      providers.set(route, provider);
    }

    function unregister(route) {
      providers.delete(route);
    }

    function has(route) {
      return providers.has(route);
    }

    function routes() {
      return Array.from(providers.keys());
    }

    function _call(route, query) {
      var fn = providers.get(route);
      if (!fn) return [];
      var out;
      try {
        out = fn(query) || [];
      } catch (e) {
        return [];
      }
      if (!Array.isArray(out)) return [];
      // Every hit MUST carry a route -- default to the provider's pane.
      return out
        .filter(function (h) { return h && typeof h.label === 'string'; })
        .map(function (h) {
          return {
            label:     h.label,
            route:     h.route   || route,
            anchor:    h.anchor  || null,
            secondary: h.secondary || null,
          };
        });
    }

    function search(query, currentRoute) {
      var q = _norm(query);
      if (!q) return { current: [], elsewhere: [] };

      var currentHits = currentRoute && providers.has(currentRoute)
        ? _rankHits(q, _call(currentRoute, q))
        : [];

      var elsewhere = [];
      providers.forEach(function (_fn, route) {
        if (route === currentRoute) return;
        elsewhere = elsewhere.concat(_call(route, q));
      });
      elsewhere = _rankHits(q, elsewhere);

      return { current: currentHits, elsewhere: elsewhere };
    }

    return {
      register:   register,
      unregister: unregister,
      has:        has,
      routes:     routes,
      search:     search,
    };
  }

  var _default = create();

  var _exports = {
    create:     create,
    register:   function (r, p) { return _default.register(r, p); },
    unregister: function (r)    { return _default.unregister(r); },
    has:        function (r)    { return _default.has(r); },
    routes:     function ()     { return _default.routes(); },
    search:     function (q, c) { return _default.search(q, c); },
    score:      score,
    // Exposed for tests / debugging only.
    _matchClass: _matchClass,
    _MATCH: { EXACT: EXACT, PREFIX: PREFIX, WORD_START: WORD_START, SUBSTRING: SUBSTRING, NO_MATCH: NO_MATCH },
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SearchRegistry = _exports;
  }
})();
