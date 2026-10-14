'use strict';

// Per-plugin "open in its own window" preference for workspace panels
// (ADR-0012 decision 2 extended; ADR-0039 Hub). Persisted per machine in
// renderer localStorage as one JSON map { pluginName: 'window' }; anything
// absent means "inline" (stay in Felix). Also holds the narrow-window rule:
// below NARROW_PX a plugin panel detaches instead of squeezing the
// conversation. Works with an empty or unavailable localStorage.
//
// Dual-mode: window.PanelPrefs in the renderer, module.exports for jest.

(function () {
  var STORAGE_KEY = 'ws-panel-pref:v1';
  var NARROW_PX = 900;

  function _load(storage) {
    try {
      var o = JSON.parse(storage.getItem(STORAGE_KEY));
      return o && typeof o === 'object' && !Array.isArray(o) ? o : {};
    } catch (e) {
      return {};
    }
  }

  // 'window' or 'inline' (the default).
  function getPref(storage, id) {
    return _load(storage)[id] === 'window' ? 'window' : 'inline';
  }

  function setPref(storage, id, pref) {
    var map = _load(storage);
    if (pref === 'window') map[id] = 'window';
    else delete map[id];
    try { storage.setItem(STORAGE_KEY, JSON.stringify(map)); } catch (e) {}
  }

  // Built-in sections have no spec to render in a detached window, so they
  // never detach. Otherwise: the saved preference, or a narrow viewport.
  function shouldDetach(opts) {
    if (!opts || opts.isBuiltin) return false;
    if (opts.pref === 'window') return true;
    return typeof opts.width === 'number' && opts.width < NARROW_PX;
  }

  // Case-insensitive substring match on title and plugin name.
  function filterPanels(panels, query) {
    var list = Array.isArray(panels) ? panels : [];
    var q = String(query || '').trim().toLowerCase();
    if (!q) return list.slice();
    return list.filter(function (p) {
      var hay = ((p && p.title) || '') + ' ' + ((p && p.plugin_name) || '');
      return hay.toLowerCase().indexOf(q) !== -1;
    });
  }

  var _exports = {
    STORAGE_KEY: STORAGE_KEY, NARROW_PX: NARROW_PX, getPref: getPref,
    setPref: setPref, shouldDetach: shouldDetach, filterPanels: filterPanels,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.PanelPrefs = _exports;
  }
})();
