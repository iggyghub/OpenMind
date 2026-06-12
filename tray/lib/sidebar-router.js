'use strict';

// Sidebar routing constants and hash parser (Issue #186, ADR-0007 Slice 2).
//
// Dual-mode export: same source feeds the Node test suite via require() AND
// the Main window renderer via a plain <script src> tag (nodeIntegration:false
// per ADR-0007 so the renderer can't require()). The UMD-ish wrapper at the
// bottom checks for module.exports and otherwise exposes the module on
// window.SidebarRouterMod.
//
// IIFE wrapper (Issue #260): prevents const VALID_ROUTES / _exports from
// landing in the global realm scope and clashing with other <script> tags.
;(function () {

const VALID_ROUTES = new Set([
  'conversation', 'queue', 'insights', 'memory',
  'permissions', 'credentials', 'plugins', 'profiles', 'settings',
]);

const DEFAULT_ROUTE = 'conversation';

// Accepts a hash string such as "#queue" or "queue" (with or without the #
// prefix) and returns the canonical route name. Falls back to DEFAULT_ROUTE
// for empty, undefined, or unrecognised values.
function routeFromHash(hash) {
  const raw = (hash || '').replace(/^#/, '');
  return VALID_ROUTES.has(raw) ? raw : DEFAULT_ROUTE;
}

// ── Dual-mode export ─────────────────────────────────────────────────────────
const _exports = { VALID_ROUTES, DEFAULT_ROUTE, routeFromHash };

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.SidebarRouterMod = _exports;
}

})();
