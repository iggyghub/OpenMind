'use strict';

// Sidebar routing constants and hash parser (Issue #186, ADR-0007 Slice 2).
//
// Dual-mode export: same source feeds the Node test suite via require() AND
// the Main window renderer via a plain <script src> tag (nodeIntegration:false
// per ADR-0007 so the renderer can't require()). The UMD-ish wrapper at the
// bottom checks for module.exports and otherwise exposes the module on
// window.SidebarRouterMod.
//
// The body is wrapped in an IIFE so the module-private declarations
// (VALID_ROUTES, _exports, ...) stay function-scoped. Classic <script src>
// tags share ONE global lexical environment, so a bare top-level
// `const _exports` here collides with the identical declaration in any other
// dual-mode lib loaded into the same page (e.g. permissions-store.js), and a
// bare `const VALID_ROUTES` collides with the Main window's inline consumer
// that destructures it -- both are page-killing redeclaration SyntaxErrors
// that kill the entire renderer script. require() gives each module its own
// scope, which is why the Node test suite never caught this.
(function () {
  const VALID_ROUTES = new Set([
    'conversation', 'quick-ask', 'queue', 'insights', 'memory',
    'permissions', 'credentials', 'plugins', 'profiles', 'settings', 'recipes',
    'models', 'conversations', 'integrations', 'job-search', 'documents',
  ]);

  const DEFAULT_ROUTE = 'conversation';

  // Accepts a hash string such as "#queue" or "queue" (with or without the #
  // prefix) and returns the canonical route name. Falls back to DEFAULT_ROUTE
  // for empty, undefined, or unrecognised values.
  function routeFromHash(hash) {
    const raw = (hash || '').replace(/^#/, '');
    return VALID_ROUTES.has(raw) ? raw : DEFAULT_ROUTE;
  }

  // ── Dual-mode export ───────────────────────────────────────────────────────
  const _exports = { VALID_ROUTES, DEFAULT_ROUTE, routeFromHash };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SidebarRouterMod = _exports;
  }
})();
