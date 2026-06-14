'use strict';

// Regression: the dual-mode libs must not leak top-level declarations into
// the shared global lexical scope when loaded as classic <script src> tags.
//
// The Main window (nodeIntegration:false) loads sidebar-router.js and
// permissions-store.js as plain <script> tags. All classic scripts on a page
// share ONE global lexical environment, so a bare top-level `const _exports`
// (or `const VALID_ROUTES`) in one collides with the next as a page-killing
// redeclaration SyntaxError -- which silently kills the entire renderer
// (dead sidebar, no WebSocket connect). The require()-based unit tests can't
// see this because each require() gets its own module scope; we reproduce the
// browser's shared scope with `vm` running both files against ONE context.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const LIB = path.join(__dirname, '..', 'lib');
const sidebarSrc = fs.readFileSync(path.join(LIB, 'sidebar-router.js'), 'utf8');
const permsSrc = fs.readFileSync(path.join(LIB, 'permissions-store.js'), 'utf8');

// A browser-like global: `window` present, `module` absent (so the libs take
// their window-export branch, exactly like the renderer).
function browserContext() {
  const ctx = { window: {} };
  vm.createContext(ctx);
  return ctx;
}

test('both dual-mode libs load into one shared global scope without collision', () => {
  const ctx = browserContext();
  // Two separate runInContext calls model two separate <script> tags sharing
  // the same global lexical environment.
  expect(() => {
    vm.runInContext(sidebarSrc, ctx, { filename: 'sidebar-router.js' });
    vm.runInContext(permsSrc, ctx, { filename: 'permissions-store.js' });
  }).not.toThrow();
});

test('each lib publishes its window global in browser mode', () => {
  const ctx = browserContext();
  vm.runInContext(sidebarSrc, ctx, { filename: 'sidebar-router.js' });
  vm.runInContext(permsSrc, ctx, { filename: 'permissions-store.js' });
  expect(ctx.window.SidebarRouterMod).toBeDefined();
  expect(ctx.window.SidebarRouterMod.DEFAULT_ROUTE).toBe('conversation');
  expect(ctx.window.PermissionsStoreMod).toBeDefined();
  expect(typeof ctx.window.PermissionsStoreMod.PermissionsStore).toBe('function');
});

test('libs do not leak private identifiers into the shared global scope', () => {
  const ctx = browserContext();
  vm.runInContext(sidebarSrc, ctx, { filename: 'sidebar-router.js' });
  vm.runInContext(permsSrc, ctx, { filename: 'permissions-store.js' });
  // A subsequent inline <script> redeclaring these names must not collide.
  expect(() =>
    vm.runInContext('const _exports = 1; const VALID_ROUTES = 2; void _exports; void VALID_ROUTES;',
      ctx, { filename: 'inline' }),
  ).not.toThrow();
});
