'use strict';

// Pinned Hub tiles (ADR-0039 decision 2): the user drags a Hub tile onto the
// sidebar to pin it as its own button, and drags the button back onto Hub to
// unpin it. Only the user pins; no code path lets a plugin add itself.
//
// State is a JSON array of plugin names in renderer localStorage (machine-
// global, ADR-0012 decision 7). Works with empty/unavailable storage.
//
// Dual-mode: window.NavPins in the renderer, module.exports for jest.

(function () {
  var STORAGE_KEY = 'nav-pins:v1';
  var EL_PREFIX = 'nav-pin-';

  function load(storage) {
    try {
      var raw = JSON.parse(storage.getItem(STORAGE_KEY));
      if (!Array.isArray(raw)) return [];
      var seen = {};
      return raw.filter(function (id) {
        if (typeof id !== 'string' || !id || seen[id]) return false;
        seen[id] = true;
        return true;
      });
    } catch (e) {
      return [];
    }
  }

  function _save(storage, list) {
    try { storage.setItem(STORAGE_KEY, JSON.stringify(list)); } catch (e) {}
    return list;
  }

  // Appends id (no-op if already pinned). Returns the new list.
  function pin(storage, id) {
    var list = load(storage);
    if (typeof id !== 'string' || !id || list.indexOf(id) !== -1) return list;
    list.push(id);
    return _save(storage, list);
  }

  function unpin(storage, id) {
    var list = load(storage).filter(function (x) { return x !== id; });
    return _save(storage, list);
  }

  // Pinned ids that still exist as plugin panels, in pinned order. A pin whose
  // plugin is currently absent is skipped, not deleted.
  function visible(storage, panels) {
    var known = {};
    (Array.isArray(panels) ? panels : []).forEach(function (p) {
      if (p && p.plugin_name) known[p.plugin_name] = p;
    });
    return load(storage).filter(function (id) { return known[id]; })
      .map(function (id) { return known[id]; });
  }

  function elId(pluginName) { return EL_PREFIX + pluginName; }

  // Inverse of elId; null when the id is not a pinned-button id.
  function pluginFromElId(id) {
    return typeof id === 'string' && id.indexOf(EL_PREFIX) === 0 && id.length > EL_PREFIX.length
      ? id.slice(EL_PREFIX.length)
      : null;
  }

  var _exports = {
    STORAGE_KEY: STORAGE_KEY, load: load, pin: pin, unpin: unpin,
    visible: visible, elId: elId, pluginFromElId: pluginFromElId,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.NavPins = _exports;
  }
})();
