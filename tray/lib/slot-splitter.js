'use strict';

// Drag-to-resize splitter between the workspace primary and secondary slots
// (S3 -- #482, ADR-0012 decision 7).
//
// Pure helpers (clamp, computeWidth, readWidth, writeWidth) are exported so
// the jest suite can test clamp and persistence without a real pointer.
// init() wires pointer events on the live DOM.
//
// Dual-mode: window.SlotSplitter in the renderer, module.exports in Node tests.
// IIFE-wrapped to stay safe in the shared renderer lexical scope.

(function () {
  var STORAGE_KEY   = 'workspace:splitter-width';
  var MIN_SECONDARY = 180;
  var MIN_PRIMARY   = 200;
  var DEFAULT_WIDTH = 480;

  function _storage() {
    try { return (typeof localStorage !== 'undefined') ? localStorage : null; }
    catch (e) { return null; }
  }

  function clamp(val, min, max) {
    if (max < min) max = min;
    return val < min ? min : val > max ? max : val;
  }

  function readWidth() {
    var s = _storage();
    if (!s) return DEFAULT_WIDTH;
    try {
      var raw = s.getItem(STORAGE_KEY);
      var n   = raw !== null ? Number(raw) : NaN;
      return isNaN(n) ? DEFAULT_WIDTH : n;
    } catch (e) { return DEFAULT_WIDTH; }
  }

  function writeWidth(w) {
    var s = _storage();
    if (!s) return;
    try { s.setItem(STORAGE_KEY, String(w)); } catch (e) {}
  }

  // Returns the clamped secondary-slot pixel width from a pointer X position.
  // containerRight = container left + containerWidth (right edge of workspace).
  // Exposed for unit tests so coordinates can be injected.
  function computeWidth(pointerX, containerRight, containerWidth, splitterPx, minSec, minPri) {
    var raw = containerRight - pointerX;
    var max = containerWidth - splitterPx - minPri;
    return clamp(raw, minSec, max);
  }

  // Wire pointer-drag resizing.
  //   splitterEl  -- the drag handle element
  //   secondaryEl -- the aside whose flex-basis is updated while dragging
  //   containerEl -- the flex parent (workspace div) for geometry
  //   opts.minSecondary / opts.minPrimary -- pixel minimums (override defaults)
  function init(splitterEl, secondaryEl, containerEl, opts) {
    if (!splitterEl || !secondaryEl || !containerEl) return;
    var minSec = (opts && opts.minSecondary != null) ? opts.minSecondary : MIN_SECONDARY;
    var minPri = (opts && opts.minPrimary   != null) ? opts.minPrimary   : MIN_PRIMARY;

    secondaryEl.style.flexBasis = readWidth() + 'px';

    var dragging = false;

    splitterEl.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      splitterEl.setPointerCapture(e.pointerId);
      dragging = true;
    });

    splitterEl.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var rect = containerEl.getBoundingClientRect();
      secondaryEl.style.flexBasis = computeWidth(
        e.clientX, rect.right, rect.width, splitterEl.offsetWidth, minSec, minPri
      ) + 'px';
    });

    function _end(e) {
      if (!dragging) return;
      dragging = false;
      var rect = containerEl.getBoundingClientRect();
      var w    = computeWidth(
        e.clientX, rect.right, rect.width, splitterEl.offsetWidth, minSec, minPri
      );
      secondaryEl.style.flexBasis = w + 'px';
      writeWidth(w);
    }

    splitterEl.addEventListener('pointerup',     _end);
    splitterEl.addEventListener('pointercancel', _end);
  }

  var _exports = {
    STORAGE_KEY:   STORAGE_KEY,
    DEFAULT_WIDTH: DEFAULT_WIDTH,
    clamp:         clamp,
    readWidth:     readWidth,
    writeWidth:    writeWidth,
    computeWidth:  computeWidth,
    init:          init,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SlotSplitter = _exports;
  }
})();
