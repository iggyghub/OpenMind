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

  // Persists the collapsed state for a pane+section pair.
  function setCollapsed(paneKey, label, collapsed) {
    try {
      if (collapsed) {
        localStorage.setItem(storageKey(paneKey, label), '1');
      } else {
        localStorage.removeItem(storageKey(paneKey, label));
      }
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

      hdr.addEventListener('click', function () {
        var nowCollapsed = hdr.classList.contains('sec-collapsed');
        _applyState(hdr, bodyEls, !nowCollapsed);
        setCollapsed(paneKey, label, !nowCollapsed);
      });

      hdr.addEventListener('keydown', function (e) {
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
  }

  var _exports = {
    init: init,
    isCollapsed: isCollapsed,
    setCollapsed: setCollapsed,
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
