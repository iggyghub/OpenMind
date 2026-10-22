'use strict';

// Generic accordion for *-section header divs (S3 — issue #286).
//
// Dual-mode export: same source feeds the Node test suite via require() AND
// the Main window renderer via window.SectionCollapse (nodeIntegration:false,
// so no require() in the renderer). Body wrapped in an IIFE to avoid
// top-level const collisions when loaded as a plain <script src> tag.

(function () {
  var PREFIX = 'sec-collapse:';
  // A global override (Settings > General > Appearance > "Keep sections
  // expanded"): once on, every section everywhere ignores its own remembered
  // collapsed state and just stays open. Persisted separately from any
  // per-section key so flipping it off later restores whatever each section
  // was individually set to before.
  var FORCE_EXPAND_KEY = PREFIX + 'force-expand';

  // Returns the localStorage key for a given pane + section label.
  function storageKey(paneKey, label) {
    return PREFIX + paneKey + ':' + label.trim().toLowerCase().replace(/\s+/g, '-');
  }

  function isForceExpand() {
    try {
      return localStorage.getItem(FORCE_EXPAND_KEY) === '1';
    } catch (e) {
      return false;
    }
  }

  function setForceExpand(on) {
    try {
      if (on) localStorage.setItem(FORCE_EXPAND_KEY, '1');
      else localStorage.removeItem(FORCE_EXPAND_KEY);
    } catch (e) {}
  }

  // Returns true when the section is persisted as collapsed. The global
  // force-expand override, when on, always wins.
  function isCollapsed(paneKey, label) {
    if (isForceExpand()) return false;
    try {
      return localStorage.getItem(storageKey(paneKey, label)) === '1';
    } catch (e) {
      return false;
    }
  }

  // Remembered state ignoring force-expand: true (collapsed), false (expanded)
  // or null (never toggled -- caller uses its own default). Lets collapsibles
  // that default CLOSED (details, integration rows) remember being opened.
  function getState(paneKey, label) {
    var v = null;
    try { v = localStorage.getItem(storageKey(paneKey, label)); } catch (e) {}
    return v === '1' ? true : v === '0' ? false : null;
  }

  // Persists the collapsed state for a pane+section pair. Expanded is stored
  // explicitly ('0') so default-collapsed sections remember being opened.
  function setCollapsed(paneKey, label, collapsed) {
    try {
      localStorage.setItem(storageKey(paneKey, label), collapsed ? '1' : '0');
    } catch (e) {}
  }

  // True when el carries a class token matching the *-section pattern
  // (e.g. perm-section, set-section, plug-section, prof-section).
  function _isSectionHdr(el) {
    var list = el.classList;
    for (var i = 0; i < list.length; i++) {
      if (/^[a-z]+-section$/.test(list[i])) return true;
    }
    return false;
  }

  // Returns the body elements for a section header: direct next siblings up to
  // (but not including) the next sibling that is itself a section header.
  function _bodyEls(hdr) {
    var els = [];
    var el = hdr.nextElementSibling;
    while (el) {
      if (_isSectionHdr(el)) break;
      els.push(el);
      el = el.nextElementSibling;
    }
    return els;
  }

  var CONTROL_SEL = 'button, a, input, select, textarea, label';

  function _isControl(target, hdr) {
    var c = target && target.closest ? target.closest(CONTROL_SEL) : null;
    return !!c && c !== hdr && hdr.contains(c);
  }

  function _applyState(hdr, bodyEls, collapsed) {
    if (collapsed) {
      hdr.classList.add('sec-collapsed');
    } else {
      hdr.classList.remove('sec-collapsed');
    }
    bodyEls.forEach(function (el) {
      if (collapsed) {
        el.setAttribute('data-sec-hidden', '');
      } else {
        el.removeAttribute('data-sec-hidden');
      }
    });
  }

  // Wire accordion behaviour on every direct-child *-section header inside
  // containerEl.  paneKey scopes the localStorage keys (e.g. 'settings').
  // Safe to call multiple times — skips already-initialised headers.
  function init(containerEl, paneKey) {
    if (!containerEl) return;
    Array.from(containerEl.children).filter(_isSectionHdr).forEach(function (hdr) {
      if (hdr.dataset.secInit) return;
      hdr.dataset.secInit = '1';

      var label   = hdr.textContent.trim();
      var bodyEls = _bodyEls(hdr);

      var chevron = document.createElement('span');
      chevron.className = 'sec-chevron';
      chevron.setAttribute('aria-hidden', 'true');
      hdr.appendChild(chevron);
      hdr.classList.add('sec-hdr');
      hdr.setAttribute('role', 'button');
      hdr.setAttribute('tabindex', '0');

      _applyState(hdr, bodyEls, isCollapsed(paneKey, label));

      hdr.addEventListener('click', function (e) {
        // A control inside the header (e.g. Model priority's Recheck) does its
        // own job; it must not also fold the section shut.
        if (_isControl(e && e.target, hdr)) return;
        var nowCollapsed = hdr.classList.contains('sec-collapsed');
        _applyState(hdr, bodyEls, !nowCollapsed);
        setCollapsed(paneKey, label, !nowCollapsed);
      });

      hdr.addEventListener('keydown', function (e) {
        if (e.target !== hdr) return;  // Enter/Space on an inner button is that button's
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          hdr.click();
        }
      });
    });
  }

  // Forces every already-initialised section header within containerEl (or
  // the whole document if omitted) visibly open right now -- for when the
  // force-expand setting is switched on and sections rendered before that
  // moment need to react immediately, not just the next ones to init().
  function forceExpandAll(containerEl) {
    var root = containerEl || document;
    var hdrs = root.querySelectorAll('.sec-hdr.sec-collapsed');
    for (var i = 0; i < hdrs.length; i++) {
      _applyState(hdrs[i], _bodyEls(hdrs[i]), false);
    }
    var dets = root.querySelectorAll(DETAILS_SEL);
    for (var j = 0; j < dets.length; j++) dets[j].open = true;
  }

  // Native <details data-collapse-key="..."> anywhere in the window: the
  // remembered state is re-applied every time one lands in the DOM (first
  // load AND every IPC re-render that rebuilds it via innerHTML), and every
  // toggle is saved. Scope = id of the nearest id'd ancestor, so the same
  // key in two panels doesn't collide. Call once per window.
  var DETAILS_SEL = 'details[data-collapse-key]';

  function _detailsScope(d) {
    var s = d.parentElement && d.parentElement.closest('[id]');
    return s ? s.id : 'doc';
  }

  function _applyDetails(d) {
    if (isForceExpand()) { d.open = true; return; }
    var st = getState(_detailsScope(d), d.getAttribute('data-collapse-key'));
    if (st !== null) d.open = !st;
  }

  function watchDetails(root) {
    root = root || document.body;
    Array.prototype.forEach.call(root.querySelectorAll(DETAILS_SEL), _applyDetails);
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        m.addedNodes.forEach(function (n) {
          if (n.nodeType !== 1) return;
          if (n.matches(DETAILS_SEL)) _applyDetails(n);
          Array.prototype.forEach.call(n.querySelectorAll(DETAILS_SEL), _applyDetails);
        });
      });
    }).observe(root, { childList: true, subtree: true });
    // toggle doesn't bubble -- capture. Reads d.open at fire time (not the
    // event's state), so a stored-collapsed re-apply that races the parser's
    // own open-attribute toggle still saves the right value.
    document.addEventListener('toggle', function (e) {
      var d = e.target;
      if (!d.matches || !d.matches(DETAILS_SEL) || !d.isConnected || isForceExpand()) return;
      setCollapsed(_detailsScope(d), d.getAttribute('data-collapse-key'), !d.open);
    }, true);
  }

  var _exports = {
    init: init,
    isCollapsed: isCollapsed,
    setCollapsed: setCollapsed,
    getState: getState,
    watchDetails: watchDetails,
    storageKey: storageKey,
    isForceExpand: isForceExpand,
    setForceExpand: setForceExpand,
    forceExpandAll: forceExpandAll,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SectionCollapse = _exports;
  }
})();
