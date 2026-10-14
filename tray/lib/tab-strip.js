'use strict';

// Generic scrollable + reorderable tab strip. One init() call wires:
//   - mouse-wheel horizontal scroll (down = right) once the strip overflows
//     its container (the scrollbar itself is hidden via CSS elsewhere);
//   - Shift+drag reordering of the tab buttons among themselves, live-swapping
//     past whichever sibling the cursor is currently over;
//   - order persistence to localStorage, keyed by each button's own text
//     (stable, and needs no markup changes at any call site), reapplied on
//     the next init().
//
// Existing per-strip click handlers (hash routing, activateSub calls, ...)
// are left completely untouched -- a window-level capture-phase listener
// swallows the click that would otherwise follow a drag, before it can reach
// any bubble-phase handler anywhere (the strip's own, or a delegated one on
// document, like Help's). Capture on window (a real ancestor of every strip)
// rather than on the strip itself sidesteps the same-target
// registration-order ambiguity a same-element capture listener would have.
//
// Dual-mode: window.TabStrip in the renderer, module.exports for jest.

(function () {
  function _tabKey(btn) {
    return (btn.textContent || '').trim();
  }

  function _saveOrder(containerEl, tabSelector, storageKey) {
    var order = Array.prototype.map.call(
      containerEl.querySelectorAll(tabSelector), _tabKey
    );
    try { localStorage.setItem(storageKey, JSON.stringify(order)); } catch (e) {}
  }

  function _applyOrder(containerEl, tabSelector, storageKey) {
    var order;
    try { order = JSON.parse(localStorage.getItem(storageKey) || 'null'); } catch (e) { order = null; }
    if (!Array.isArray(order)) return;
    var byKey = {};
    containerEl.querySelectorAll(tabSelector).forEach(function (b) { byKey[_tabKey(b)] = b; });
    order.forEach(function (k) { if (byKey[k]) containerEl.appendChild(byKey[k]); });
  }

  // opts: { tabSelector (required, e.g. '.lib-tab'), storageKey (default
  // derived from containerEl.id) }.
  function init(containerEl, opts) {
    if (!containerEl) return;
    var o = opts || {};
    var tabSelector = o.tabSelector;
    if (!tabSelector) return;
    var storageKey = o.storageKey || ('tab-strip-order:' + (containerEl.id || tabSelector));

    containerEl.addEventListener('wheel', function (e) {
      if (containerEl.scrollWidth <= containerEl.clientWidth) return;
      e.preventDefault();
      containerEl.scrollLeft += e.deltaY;
    }, { passive: false });

    _applyOrder(containerEl, tabSelector, storageKey);

    var dragBtn = null, dragged = false;
    containerEl.addEventListener('mousedown', function (e) {
      var btn = e.target.closest(tabSelector);
      if (!btn || !e.shiftKey) return;
      dragBtn = btn;
      dragged = false;
      containerEl.classList.add('is-dragging');
      btn.classList.add('is-drag-source');
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragBtn) return;
      dragged = true;
      var atPoint = document.elementFromPoint(e.clientX, e.clientY);
      var over = atPoint && atPoint.closest && atPoint.closest(tabSelector);
      if (over && over !== dragBtn && over.parentElement === containerEl) {
        var before = over.compareDocumentPosition(dragBtn) & Node.DOCUMENT_POSITION_FOLLOWING;
        containerEl.insertBefore(dragBtn, before ? over : over.nextSibling);
      }
    });
    window.addEventListener('mouseup', function () {
      if (!dragBtn) return;
      dragBtn.classList.remove('is-drag-source');
      containerEl.classList.remove('is-dragging');
      if (dragged) _saveOrder(containerEl, tabSelector, storageKey);
      dragBtn = null;
    });
    window.addEventListener('click', function (e) {
      if (!dragged || !containerEl.contains(e.target)) return;
      dragged = false;
      e.stopPropagation();
      e.preventDefault();
    }, true);
  }

  var _exports = { init: init, _tabKey: _tabKey, _saveOrder: _saveOrder, _applyOrder: _applyOrder };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.TabStrip = _exports;
  }
})();
