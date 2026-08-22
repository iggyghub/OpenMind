'use strict';

// Workspace shell state layer (S2 -- #481, ADR-0012 decision 1).
//
// Primary slot permanently holds the Conversation; a secondary slot beside it
// holds zero or more panels with a tab strip when several are open. Which
// panels are open, and which tab is active, persist in renderer localStorage.
//
// This module owns the state model only -- the DOM wiring is inline in
// main.html and calls readState/open/close/activate to drive re-renders.
// Keeping the state layer pure lets it run under the Node test suite without
// jsdom, matching the sidebar-collapse.js pattern.
//
// Dual-mode: same source feeds the renderer via <script src> (exports on
// window.WorkspaceMod) and the Node tests via require(). IIFE-wrapped so the
// private consts stay function-scoped (see renderer-script-globals.test.js).

(function () {
  var STORAGE_KEY_OPEN   = 'workspace:open';
  var STORAGE_KEY_ACTIVE = 'workspace:active';

  function _storage() {
    try { return (typeof localStorage !== 'undefined') ? localStorage : null; }
    catch (e) { return null; }
  }

  function _readOpen() {
    var s = _storage();
    if (!s) return [];
    try {
      var raw = s.getItem(STORAGE_KEY_OPEN);
      if (!raw) return [];
      var arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return [];
      var seen = {};
      var out = [];
      for (var i = 0; i < arr.length; i++) {
        var id = String(arr[i]);
        if (!id || seen[id]) continue;
        seen[id] = true;
        out.push(id);
      }
      return out;
    } catch (e) { return []; }
  }

  function _readActive() {
    var s = _storage();
    if (!s) return null;
    try {
      var raw = s.getItem(STORAGE_KEY_ACTIVE);
      return raw || null;
    } catch (e) { return null; }
  }

  function _writeOpen(open) {
    var s = _storage();
    if (!s) return;
    try {
      if (!open || open.length === 0) {
        s.removeItem(STORAGE_KEY_OPEN);
      } else {
        s.setItem(STORAGE_KEY_OPEN, JSON.stringify(open));
      }
    } catch (e) {}
  }

  function _writeActive(active) {
    var s = _storage();
    if (!s) return;
    try {
      if (!active) {
        s.removeItem(STORAGE_KEY_ACTIVE);
      } else {
        s.setItem(STORAGE_KEY_ACTIVE, String(active));
      }
    } catch (e) {}
  }

  // Reads the persisted state and repairs any drift (active not in open list,
  // duplicates, empty ids). Returns a fresh {open, active} snapshot.
  function readState() {
    var open   = _readOpen();
    var active = _readActive();
    if (open.length === 0) return { open: [], active: null };
    if (!active || open.indexOf(active) === -1) active = open[0];
    return { open: open, active: active };
  }

  // Opens a panel: adds it to the open list if absent, activates it, persists.
  // Returns the resulting state.
  function open(panelId) {
    var id = String(panelId || '');
    if (!id) return readState();
    var state = readState();
    if (state.open.indexOf(id) === -1) state.open.push(id);
    state.active = id;
    _writeOpen(state.open);
    _writeActive(state.active);
    return state;
  }

  // Closes a panel: removes it from the open list; if it was active, activates
  // the previous tab (or null when none remain). Persists. Returns the
  // resulting state.
  function close(panelId) {
    var id = String(panelId || '');
    if (!id) return readState();
    var state = readState();
    var idx   = state.open.indexOf(id);
    if (idx === -1) return state;
    state.open.splice(idx, 1);
    if (state.active === id) {
      state.active = state.open.length === 0
        ? null
        : state.open[Math.max(0, idx - 1)];
    }
    _writeOpen(state.open);
    _writeActive(state.active);
    return state;
  }

  // Activates an already-open panel. Opens it if not already in the list
  // (equivalent to open() in that case). Persists. Returns the resulting state.
  function activate(panelId) {
    var id = String(panelId || '');
    if (!id) return readState();
    var state = readState();
    if (state.open.indexOf(id) === -1) return open(id);
    state.active = id;
    _writeActive(state.active);
    return state;
  }

  function isOpen(panelId) {
    return readState().open.indexOf(String(panelId || '')) !== -1;
  }

  // #829 -- toggle a panel open exclusively (closing every other open
  // panel first, so it replaces whatever was showing instead of adding a
  // tab beside it) or closed if it's already the sole shown panel. Built
  // for the THINKING pill, which should act as a real open/close toggle
  // rather than an open-only control like the +Panel dropdown.
  function toggleExclusive(panelId) {
    var id = String(panelId || '');
    if (!id) return readState();
    var state = readState();
    var isShown = state.active === id && state.open.indexOf(id) !== -1;
    if (isShown) return close(id);
    state.open.forEach(function (openId) { if (openId !== id) close(openId); });
    return open(id);
  }

  var _exports = {
    STORAGE_KEY_OPEN:   STORAGE_KEY_OPEN,
    STORAGE_KEY_ACTIVE: STORAGE_KEY_ACTIVE,
    readState:       readState,
    open:            open,
    close:           close,
    activate:        activate,
    isOpen:          isOpen,
    toggleExclusive: toggleExclusive,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.WorkspaceMod = _exports;
  }
})();
