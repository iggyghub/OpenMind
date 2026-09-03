'use strict';

// Sidebar routing constants and hash parser (Issue #186, ADR-0007 Slice 2).
// S5 #473: route collapse 16->4 nav sections + hash redirects.
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
  // Six primary nav sections (Trading promoted from a Library sub-tab,
  // #864 follow-up; Log added S26/#879 -- the Activity Log) + profiles
  // (kept valid so #profiles / first_run flow keeps working without a
  // nav button).
  const VALID_ROUTES = new Set([
    'conversation', 'harness', 'help', 'library', 'log', 'profiles', 'settings', 'trading',
  ]);

  const DEFAULT_ROUTE = 'conversation';

  // Maps every pre-collapse route to its new canonical destination.
  // Values may be 'base' or 'base/sub' (subFromHash extracts the sub-part).
  const HASH_REDIRECTS = {
    'quick-ask':     'conversation',
    'queue':         'conversation',
    'conversations': 'conversation',
    'insights':      'library',
    'memory':        'library',
    'recipes':       'library',
    'job-search':    'library',
    'documents':     'library',
    'plugins':       'harness',
    'integrations':  'settings/signin',
    'credentials':   'settings/signin',
    'permissions':   'settings/permissions',
    'models':        'settings/models',
  };

  // Accepts a hash string such as "#queue", "queue", "#harness/my_plugin"
  // (with or without the # prefix) and returns the canonical base route.
  // Old hashes follow HASH_REDIRECTS; sub-routes like "#harness/name" strip
  // the sub-part and return the base. Falls back to DEFAULT_ROUTE for empty,
  // undefined, or unrecognised values.
  function routeFromHash(hash) {
    const raw  = (hash || '').replace(/^#/, '');
    const base = raw.split('/')[0];
    if (VALID_ROUTES.has(base)) return base;
    const redirect = HASH_REDIRECTS[base];
    if (redirect) return redirect.split('/')[0];
    return DEFAULT_ROUTE;
  }

  // Returns the sub-route portion for routes like "#harness/plugin_name"
  // or redirects that target "base/sub" (e.g. "settings/models" for "#models").
  // Returns null when there is no sub-route.
  function subFromHash(hash) {
    const raw   = (hash || '').replace(/^#/, '');
    const parts = raw.split('/');
    const base  = parts[0];
    if (VALID_ROUTES.has(base)) return parts[1] || null;
    const redirect = HASH_REDIRECTS[base];
    if (redirect) return redirect.split('/')[1] || null;
    return null;
  }

  // ── Dual-mode export ───────────────────────────────────────────────────────
  const _exports = { VALID_ROUTES, DEFAULT_ROUTE, HASH_REDIRECTS, routeFromHash, subFromHash };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SidebarRouterMod = _exports;
  }
})();
