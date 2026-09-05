'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

const { checkLocalPath, validateSaveBody } = require('../server.js');

// ---- checkLocalPath: path-traversal containment ----

test('traversal: plain ../ is blocked', () => {
  assert.equal(checkLocalPath('../etc/passwd'), null);
});

test('traversal: URL-decoded ..%2Fetc%2Fpasswd is blocked', () => {
  // %2F decodes to /, giving ../etc/passwd after decodeURIComponent (same as handleLocal does)
  assert.equal(checkLocalPath(decodeURIComponent('..%2Fetc%2Fpasswd')), null);
});

test('traversal: URL-decoded %5c..%5c (backslash) is blocked', () => {
  // %5c decodes to \; path.resolve treats \ as separator on Windows
  assert.equal(checkLocalPath(decodeURIComponent('%5c..%5cWindows%5cSystem32')), null);
});

test('traversal: leading %2f makes path absolute outside ROOT (directory-boundary bug)', () => {
  // %2f decodes to /, so decodeURIComponent('%2fOpenMindEvil%2fsecret.html') = '/OpenMindEvil/secret.html'
  // path.resolve(ROOT, '/OpenMindEvil/secret.html') resolves to a drive-root path outside ROOT.
  // The old full.startsWith(ROOT) check (without sep) was insufficient: a sibling directory
  // whose name starts with the same prefix as ROOT (e.g. C:\OpenMindEvil) would have passed.
  // With the sep check it is correctly rejected.
  assert.equal(checkLocalPath(decodeURIComponent('%2fOpenMindEvil%2fsecret.html')), null);
});

test('traversal: deep ../ chain is blocked', () => {
  assert.equal(checkLocalPath('../../../../etc/shadow'), null);
});

test('traversal: valid repo-relative path is allowed', () => {
  assert.ok(checkLocalPath('tray/windows/main.html') !== null);
});

test('traversal: nested valid path is allowed', () => {
  assert.ok(checkLocalPath('tools/ui-editor/server.js') !== null);
});

// ---- validateSaveBody ----

test('validateSaveBody: null input returns error', () => {
  assert.ok(validateSaveBody(null) !== null);
});

test('validateSaveBody: array input returns error', () => {
  assert.ok(validateSaveBody([]) !== null);
});

test('validateSaveBody: missing key returns error', () => {
  assert.ok(validateSaveBody({ overrides: {} }) !== null);
});

test('validateSaveBody: empty string key returns error', () => {
  assert.ok(validateSaveBody({ key: '', overrides: {} }) !== null);
});

test('validateSaveBody: numeric key returns error', () => {
  assert.ok(validateSaveBody({ key: 42, overrides: {} }) !== null);
});

test('validateSaveBody: overrides missing returns error', () => {
  assert.ok(validateSaveBody({ key: 'k' }) !== null);
});

test('validateSaveBody: overrides as array returns error', () => {
  assert.ok(validateSaveBody({ key: 'k', overrides: [] }) !== null);
});

test('validateSaveBody: overrides as string returns error', () => {
  assert.ok(validateSaveBody({ key: 'k', overrides: 'bad' }) !== null);
});

test('validateSaveBody: valid body returns null', () => {
  assert.equal(validateSaveBody({ key: 'local_page.html', overrides: { 'BODY1>P0': { style: { color: 'red' } } } }), null);
});

test('validateSaveBody: empty overrides object is valid', () => {
  assert.equal(validateSaveBody({ key: 'k', overrides: {} }), null);
});
