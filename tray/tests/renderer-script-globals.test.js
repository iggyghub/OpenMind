'use strict';

// Regression: the dual-mode libs must not leak top-level declarations into
// the shared global lexical scope when loaded as classic <script src> tags.
//
// A renderer (nodeIntegration:false) can load these libs as plain <script>
// tags. All classic scripts on a page share ONE global lexical environment,
// so a bare top-level `const _exports` (or `const VALID_ROUTES`) in one
// collides with the next as a page-killing redeclaration SyntaxError -- which
// silently kills the entire renderer (dead sidebar, no WebSocket connect). The
// require()-based unit tests can't see this because each require() gets its own
// module scope; we reproduce the browser's shared scope with `vm` running every
// file against ONE context.
//
// sidebar-router.js + permissions-store.js were the libs that actually killed
// the Main window (#263/#264). consent-manager, modal-manager, model-menu and
// notification-manager are covered here as preventive hardening: each is now
// dual-mode + IIFE-wrapped so it stays safe to drop into any window later.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const LIB = path.join(__dirname, '..', 'lib');

// Every dual-mode lib (IIFE-wrapped, exports on window.*Mod in browser mode).
// Each entry: the file plus the window global it must publish and a sentinel
// assertion proving the export survived the load.
const DUAL_MODE_LIBS = [
  {
    file: 'sidebar-router.js',
    global: 'SidebarRouterMod',
    check: (mod) => expect(mod.DEFAULT_ROUTE).toBe('conversation'),
  },
  {
    file: 'permissions-store.js',
    global: 'PermissionsStoreMod',
    check: (mod) => expect(typeof mod.PermissionsStore).toBe('function'),
  },
  {
    file: 'consent-manager.js',
    global: 'ConsentManagerMod',
    check: (mod) => expect(typeof mod.ConsentManager).toBe('function'),
  },
  {
    file: 'modal-manager.js',
    global: 'ModalManagerMod',
    check: (mod) => expect(typeof mod.ModalManager).toBe('function'),
  },
  {
    file: 'model-menu.js',
    global: 'ModelMenuMod',
    check: (mod) => expect(typeof mod.buildModelSubmenu).toBe('function'),
  },
  {
    file: 'notification-manager.js',
    global: 'NotificationManagerMod',
    check: (mod) => expect(typeof mod.NotificationManager).toBe('function'),
  },
  {
    file: 'section-collapse.js',
    global: 'SectionCollapse',
    check: (mod) => expect(typeof mod.init).toBe('function'),
  },
  {
    file: 'search-registry.js',
    global: 'SearchRegistry',
    check: (mod) => expect(typeof mod.search).toBe('function'),
  },
  {
    file: 'sidebar-collapse.js',
    global: 'SidebarCollapse',
    check: (mod) => expect(typeof mod.toggle).toBe('function'),
  },
];

const SOURCES = DUAL_MODE_LIBS.map((lib) => ({
  ...lib,
  src: fs.readFileSync(path.join(LIB, lib.file), 'utf8'),
}));

// A browser-like global: `window` present, `module` absent (so the libs take
// their window-export branch, exactly like the renderer).
function browserContext() {
  const ctx = { window: {} };
  vm.createContext(ctx);
  return ctx;
}

// Run every lib into one shared context, modelling N separate <script> tags
// that share the same global lexical environment.
function loadAll(ctx) {
  for (const lib of SOURCES) {
    vm.runInContext(lib.src, ctx, { filename: lib.file });
  }
}

test('all dual-mode libs load into one shared global scope without collision', () => {
  const ctx = browserContext();
  expect(() => loadAll(ctx)).not.toThrow();
});

test('each lib publishes its window global in browser mode', () => {
  const ctx = browserContext();
  loadAll(ctx);
  for (const lib of SOURCES) {
    const mod = ctx.window[lib.global];
    expect(mod).toBeDefined();
    lib.check(mod);
  }
});

test('libs do not leak private identifiers into the shared global scope', () => {
  const ctx = browserContext();
  loadAll(ctx);
  // A subsequent inline <script> redeclaring these names must not collide --
  // proving the IIFE wrappers kept `_exports` (and friends) function-scoped.
  expect(() =>
    vm.runInContext('const _exports = 1; const VALID_ROUTES = 2; void _exports; void VALID_ROUTES;',
      ctx, { filename: 'inline' }),
  ).not.toThrow();
});
