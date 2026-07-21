'use strict';

// Sidebar collapse: shrinks the main .sidebar to an icon rail (S1 -- #480).
// Toggle via init()-wired button or Ctrl+B (wired in main.html inline script).
// Collapsed state persists in localStorage.
//
// Dual-mode: window.SidebarCollapse in the renderer, module.exports for jest.

(function () {
  var STORAGE_KEY = 'sidebar-collapse:collapsed';

  function isCollapsed() {
    try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch (e) { return false; }
  }

  function setCollapsed(collapsed) {
    try {
      if (collapsed) {
        localStorage.setItem(STORAGE_KEY, '1');
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch (e) {}
  }

  // Toggles is-collapsed on sidebarEl, persists, and returns the new state.
  function toggle(sidebarEl) {
    var next = !sidebarEl.classList.contains('is-collapsed');
    if (next) {
      sidebarEl.classList.add('is-collapsed');
    } else {
      sidebarEl.classList.remove('is-collapsed');
    }
    setCollapsed(next);
    return next;
  }

  // Applies persisted state and wires the .sidebar-collapse-toggle button.
  function init(sidebarEl) {
    if (!sidebarEl) return;
    if (isCollapsed()) sidebarEl.classList.add('is-collapsed');
    var btn = sidebarEl.querySelector('.sidebar-collapse-toggle');
    if (btn) btn.addEventListener('click', function () { toggle(sidebarEl); });
  }

  var _exports = { STORAGE_KEY: STORAGE_KEY, isCollapsed: isCollapsed, setCollapsed: setCollapsed, toggle: toggle, init: init };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SidebarCollapse = _exports;
  }
})();
