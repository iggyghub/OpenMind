'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

const { renderHtml, writeOverrides, resetOverrides, sanitizeKey } = require('../server.js');
const { bake } = require('../html-bake.js');

// renderHtml(skey, fullPath) returns bake(source, overrides) without touching disk.
// Tests use a temp file within os.tmpdir() -- renderHtml accepts any absolute path
// that was pre-validated by the caller; tests bypass the checkLocalPath guard to
// exercise the core bake-to-string logic in isolation.

const TMP_HTML = path.join(os.tmpdir(), 'uieditor-render-test.html');
const SOURCE = '<!doctype html><html><head><title>T</title></head><body><p>hello</p></body></html>';
// <body> is child index 1 of <html> (after <head>), so path is BODY1>P0
const OVERRIDES = { 'BODY1>P0': { style: { color: '#ff0000' }, text: 'world' } };

test('renderHtml: no overrides returns source unchanged', () => {
  fs.writeFileSync(TMP_HTML, SOURCE, 'utf8');
  const key = 'test_render_empty_' + Date.now();
  const result = renderHtml(sanitizeKey(key), TMP_HTML);
  assert.equal(result, bake(SOURCE, {}));
  fs.unlinkSync(TMP_HTML);
});

test('renderHtml: applies overrides matching S2 bake output', () => {
  fs.writeFileSync(TMP_HTML, SOURCE, 'utf8');
  const key = 'test_render_ovs_' + Date.now();
  writeOverrides(sanitizeKey(key), OVERRIDES);
  const result = renderHtml(sanitizeKey(key), TMP_HTML);
  assert.equal(result, bake(SOURCE, OVERRIDES));
  assert.match(result, /color:#ff0000/);
  assert.match(result, /world/);
  resetOverrides(sanitizeKey(key));
  fs.unlinkSync(TMP_HTML);
});

test('renderHtml: does not write to disk (source file unchanged)', () => {
  fs.writeFileSync(TMP_HTML, SOURCE, 'utf8');
  const key = 'test_render_nodisk_' + Date.now();
  writeOverrides(sanitizeKey(key), OVERRIDES);
  renderHtml(sanitizeKey(key), TMP_HTML);
  assert.equal(fs.readFileSync(TMP_HTML, 'utf8'), SOURCE);
  resetOverrides(sanitizeKey(key));
  fs.unlinkSync(TMP_HTML);
});
