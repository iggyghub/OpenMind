'use strict';

// Drag-to-resize handle on the right edge of the left nav sidebar. Sibling to
// slot-splitter.js (S3 -- #482) but mirrored: the sidebar is anchored to the
// LEFT edge, so width is measured from the sidebar's own left, not from a
// container's right edge like the workspace splitter.
//
// Coexists with sidebar-collapse.js (S1 -- #480), which toggles the
// `is-collapsed` class both from its button and from the Ctrl+B hotkey in
// main.html -- an inline `style.width` set by dragging would otherwise beat
// the collapsed-state CSS rule (`.sidebar.is-collapsed { width: 48px }`)
// since inline style always wins over a class selector. A MutationObserver on
// the class attribute (rather than hooking the button click, which the
// hotkey path bypasses) covers both triggers from one place.
//
// Dual-mode: window.SidebarResize in the renderer, module.exports for jest.

(function () {
  var STORAGE_KEY = 'sidebar:width';
  var MIN = 140;
  var MAX = 360;
  var DEFAULT_WIDTH = 180;

  function _storage() {
    try { return (typeof localStorage !== 'undefined') ? localStorage : null; }
    catch (e) { return null; }
  }

  function clamp(w) { return w < MIN ? MIN : w > MAX ? MAX : w; }

  function readWidth() {
    var s = _storage();
    if (!s) return DEFAULT_WIDTH;
    try {
      var raw = s.getItem(STORAGE_KEY);
      var n = raw !== null ? Number(raw) : NaN;
      return isNaN(n) ? DEFAULT_WIDTH : clamp(n);
    } catch (e) { return DEFAULT_WIDTH; }
  }

  function writeWidth(w) {
    var s = _storage();
    if (!s) return;
    try { s.setItem(STORAGE_KEY, String(w)); } catch (e) {}
  }

  // Wire pointer-drag resizing. splitterEl is a thin handle placed just after
  // the sidebar in DOM order; sidebarEl is the .sidebar element itself.
  function init(splitterEl, sidebarEl) {
    if (!splitterEl || !sidebarEl) return;

    function syncWidth() {
      sidebarEl.style.width = sidebarEl.classList.contains('is-collapsed') ? '' : (readWidth() + 'px');
    }
    syncWidth();

    if (typeof MutationObserver !== 'undefined') {
      new MutationObserver(syncWidth).observe(sidebarEl, { attributes: true, attributeFilter: ['class'] });
    }

    var dragging = false;
    splitterEl.addEventListener('pointerdown', function (e) {
      if (sidebarEl.classList.contains('is-collapsed')) return;
      e.preventDefault();
      splitterEl.setPointerCapture(e.pointerId);
      dragging = true;
    });
    splitterEl.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var rect = sidebarEl.getBoundingClientRect();
      sidebarEl.style.width = clamp(e.clientX - rect.left) + 'px';
    });
    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      try { splitterEl.releasePointerCapture(e.pointerId); } catch (ex) {}
      writeWidth(parseFloat(sidebarEl.style.width) || DEFAULT_WIDTH);
    }
    splitterEl.addEventListener('pointerup', endDrag);
    splitterEl.addEventListener('pointercancel', endDrag);
  }

  var _exports = {
    STORAGE_KEY: STORAGE_KEY, MIN: MIN, MAX: MAX, DEFAULT_WIDTH: DEFAULT_WIDTH,
    clamp: clamp, readWidth: readWidth, writeWidth: writeWidth, init: init,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SidebarResize = _exports;
  }
})();
