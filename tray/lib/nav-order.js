'use strict';

// Sidebar nav order: drag-and-drop (and Alt+Up/Down) reordering of the
// sidebar buttons, persisted per machine in renderer localStorage (ADR-0039
// decision 3, ADR-0012 decision 7). Built-in routes keep their identity; only
// their order is user-owned. Works with an empty or unavailable localStorage.
//
// Dual-mode: window.NavOrder in the renderer, module.exports for jest. The
// pure order helpers (applyOrder / move / step) are shared with the later
// Hub pin/unpin slice (#1423).

(function () {
  var STORAGE_KEY = 'nav-order:v1';

  function keyOf(el) {
    return (el && el.dataset && el.dataset.route) || (el && el.id) || '';
  }

  // Saved order first (unknown ids dropped), then any ids the saved list does
  // not mention, in their default order. Always returns each id exactly once.
  function applyOrder(ids, saved) {
    var known = {};
    ids.forEach(function (id) { known[id] = true; });
    var seen = {};
    var out = [];
    (Array.isArray(saved) ? saved : []).forEach(function (id) {
      if (known[id] && !seen[id]) { seen[id] = true; out.push(id); }
    });
    ids.forEach(function (id) {
      if (!seen[id]) { seen[id] = true; out.push(id); }
    });
    return out;
  }

  // Moves id next to targetId (after it when `after` is true). Returns a new
  // array; unchanged copy if either id is unknown or they are the same.
  function move(order, id, targetId, after) {
    if (id === targetId || order.indexOf(id) < 0 || order.indexOf(targetId) < 0) {
      return order.slice();
    }
    var rest = order.filter(function (x) { return x !== id; });
    var i = rest.indexOf(targetId);
    rest.splice(after ? i + 1 : i, 0, id);
    return rest;
  }

  // Moves id by delta positions (clamped: out-of-range is a no-op).
  function step(order, id, delta) {
    var i = order.indexOf(id);
    var j = i + delta;
    if (i < 0 || j < 0 || j >= order.length) return order.slice();
    var out = order.slice();
    out.splice(i, 1);
    out.splice(j, 0, id);
    return out;
  }

  function load(storage) {
    try {
      var raw = storage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function save(storage, order) {
    try { storage.setItem(STORAGE_KEY, JSON.stringify(order)); } catch (e) {}
  }

  // Applies the saved order to navEl's .nav-item children and wires drag and
  // Alt+Up/Down. `storage` defaults to window.localStorage.
  function init(navEl, storage) {
    if (!navEl) return;
    var store = storage;
    if (!store) {
      try { store = window.localStorage; } catch (e) { store = null; }
    }

    function items() {
      return Array.prototype.slice.call(navEl.querySelectorAll('.nav-item'))
        .filter(function (el) { return keyOf(el); });
    }
    function currentOrder() { return items().map(keyOf); }
    function commit(order) {
      var byId = {};
      items().forEach(function (el) { byId[keyOf(el)] = el; });
      order.forEach(function (id) { if (byId[id]) navEl.appendChild(byId[id]); });
      if (store) save(store, order);
    }

    if (store) {
      var ids = currentOrder();
      var applied = applyOrder(ids, load(store));
      if (applied.join('|') !== ids.join('|')) commit(applied);
    }
    items().forEach(function (el) { el.setAttribute('draggable', 'true'); });

    var dragId = null;
    function clearMarks() {
      items().forEach(function (el) {
        el.classList.remove('nav-dragging', 'nav-drop-before', 'nav-drop-after');
      });
    }
    function itemAt(target) {
      return target && target.closest ? target.closest('.nav-item') : null;
    }

    navEl.addEventListener('dragstart', function (e) {
      var el = itemAt(e.target);
      if (!el || !keyOf(el)) return;
      dragId = keyOf(el);
      el.classList.add('nav-dragging');
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', dragId); } catch (err) {}
      }
    });
    // Where a drop at this event would land: {el, after}, or null. Over a
    // button: before/after it by the cursor's half. Over empty space beyond
    // either end of the list (the sidebar is taller than the buttons): snap
    // to the last button (after) / first button (before).
    function resolveDrop(e) {
      if (!dragId) return null;
      var el = itemAt(e.target);
      if (el && keyOf(el)) {
        if (keyOf(el) === dragId) return null;
        var r = el.getBoundingClientRect();
        return { el: el, after: e.clientY > r.top + r.height / 2 };
      }
      var list = items();
      if (!list.length || typeof e.clientY !== 'number') return null;
      var first = list[0];
      var last = list[list.length - 1];
      if (e.clientY >= last.getBoundingClientRect().bottom && keyOf(last) !== dragId) {
        return { el: last, after: true };
      }
      if (e.clientY <= first.getBoundingClientRect().top && keyOf(first) !== dragId) {
        return { el: first, after: false };
      }
      return null;
    }

    // Listen on the whole sidebar (when there is one) so the empty space
    // below the buttons is a valid drop target.
    var zone = (navEl.closest && navEl.closest('.sidebar')) || navEl;
    zone.addEventListener('dragover', function (e) {
      var t = resolveDrop(e);
      if (!t) return;
      e.preventDefault();
      items().forEach(function (x) { x.classList.remove('nav-drop-before', 'nav-drop-after'); });
      t.el.classList.add(t.after ? 'nav-drop-after' : 'nav-drop-before');
    });
    zone.addEventListener('drop', function (e) {
      var t = resolveDrop(e);
      if (!t) { clearMarks(); dragId = null; return; }
      e.preventDefault();
      commit(move(currentOrder(), dragId, keyOf(t.el), t.after));
      clearMarks();
      dragId = null;
    });
    zone.addEventListener('dragend', function () { clearMarks(); dragId = null; });

    navEl.addEventListener('keydown', function (e) {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      var el = itemAt(e.target);
      if (!el || !keyOf(el)) return;
      e.preventDefault();
      commit(step(currentOrder(), keyOf(el), e.key === 'ArrowUp' ? -1 : 1));
      if (el.focus) el.focus();
    });
  }

  var _exports = {
    STORAGE_KEY: STORAGE_KEY, keyOf: keyOf, applyOrder: applyOrder, move: move,
    step: step, load: load, save: save, init: init,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.NavOrder = _exports;
  }
})();
