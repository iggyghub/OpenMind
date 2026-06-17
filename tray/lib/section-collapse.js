'use strict';

// Generic accordion for *-section header divs (S3 — issue #286).
//
// Dual-mode export: same source feeds the Node test suite via require() AND
// the Main window renderer via window.SectionCollapse (nodeIntegration:false,
// so no require() in the renderer). Body wrapped in an IIFE to avoid
// top-level const collisions when loaded as a plain <script src> tag.

(function () {
  var PREFIX = 'sec-collapse:';

  // Returns the localStorage key for a given pane + section label.
  function storageKey(paneKey, label) {
    return PREFIX + paneKey + ':' + label.trim().toLowerCase().replace(/\s+/g, '-');
  }

  // Returns true when the section is persisted as collapsed.
  function isCollapsed(paneKey, label) {
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

  var _exports = { init: init, isCollapsed: isCollapsed, setCollapsed: setCollapsed, storageKey: storageKey };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SectionCollapse = _exports;
  }
})();
