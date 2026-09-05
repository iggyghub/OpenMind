'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

const { sanitizeKey, injectIntoHtml, readOverrides, writeOverrides, resetOverrides, validateSaveBody } =
  require('../server.js');

// sanitizeKey
test('sanitizeKey: replaces unsafe chars, truncates at 150', () => {
  assert.equal(sanitizeKey('local:tray/windows/main.html'), 'local_tray_windows_main.html');
  assert.equal(sanitizeKey('remote:https://x.com/p?q=1'), 'remote_https___x.com_p_q_1');
  assert.equal(sanitizeKey('a'.repeat(200)).length, 150);
  assert.equal(sanitizeKey('ok_id-1.2'), 'ok_id-1.2'); // safe chars pass through
});

// injectIntoHtml
test('injectIntoHtml: inserts base href into existing head', () => {
  const html = '<html><head></head><body></body></html>';
  const out = injectIntoHtml(html, '/inject.js?key=x', 'https://example.com/');
  assert.match(out, /<head><base href="https:\/\/example\.com\/">/);
});

test('injectIntoHtml: creates head with base href when head tag absent', () => {
  const html = '<html><body></body></html>';
  const out = injectIntoHtml(html, '/inject.js', 'https://example.com/');
  assert.match(out, /<head><base href="https:\/\/example\.com\/">/);
});

test('injectIntoHtml: omits base href when baseHref is null', () => {
  const out = injectIntoHtml('<html><head></head><body></body></html>', '/inject.js', null);
  assert.doesNotMatch(out, /<base /);
});

test('injectIntoHtml: removes pre-existing base tag, inserts new one', () => {
  const html = '<html><head><base href="old://"></head><body></body></html>';
  const out = injectIntoHtml(html, '/inject.js', 'https://new.com/');
  const bases = out.match(/<base /g);
  assert.equal(bases && bases.length, 1, 'exactly one base tag');
  assert.match(out, /<base href="https:\/\/new\.com\/">/);
});

test('injectIntoHtml: strips CSP meta (case-insensitive)', () => {
  const html = '<head><meta http-equiv="Content-Security-Policy" content="default-src none"></head><body></body>';
  const out = injectIntoHtml(html, '/inject.js', null);
  assert.doesNotMatch(out, /content-security-policy/i);
});

test('injectIntoHtml: inserts script before </body>', () => {
  const html = '<html><body><p>hi</p></body></html>';
  const out = injectIntoHtml(html, '/inject.js?key=k', null);
  assert.match(out, /<script src="\/inject\.js\?key=k"><\/script><\/body>/i);
});

test('injectIntoHtml: appends script when no </body> present', () => {
  const out = injectIntoHtml('<html><body><p>hi</p>', '/inject.js', null);
  assert.match(out, /<script src="\/inject\.js"><\/script>$/);
});

test('injectIntoHtml: local path script tag (absolute via inject.js?key=...)', () => {
  const out = injectIntoHtml('<html><body></body></html>', 'http://localhost:4545/inject.js?key=local_test', null);
  assert.match(out, /src="http:\/\/localhost:4545\/inject\.js\?key=local_test"/);
});

// save / load / reset round-trip
test('save -> load -> reset round-trip', () => {
  const key = 'test_roundtrip_' + Date.now();
  const data = { 'BODY0>P0': { style: { color: '#ff0000' }, text: 'hello' } };
  writeOverrides(key, data);
  assert.deepEqual(readOverrides(key), data);
  resetOverrides(key);
  assert.deepEqual(readOverrides(key), {});
});

test('readOverrides returns {} for missing key', () => {
  assert.deepEqual(readOverrides('nonexistent_key_xyz_' + Date.now()), {});
});

test('writeOverrides overwrites previous data for same key', () => {
  const key = 'test_overwrite_' + Date.now();
  writeOverrides(key, { a: 1 });
  writeOverrides(key, { b: 2 });
  assert.deepEqual(readOverrides(key), { b: 2 });
  resetOverrides(key);
});

// insert-op round trip (S7)
test('insert-op override: save -> load -> reset round trip', () => {
  const key = 'test_insert_' + Date.now();
  const data = {
    'HTML0>BODY0>P0': { style: { color: '#ff0000' } },
    'ins:0': {
      insert: { targetId: 'HTML0>BODY0>P0', op: 'before', tag: 'H2', text: 'New Heading', attrs: {} },
      style: { fontSize: '24px' }
    }
  };
  writeOverrides(key, data);
  assert.deepEqual(readOverrides(key), data);
  resetOverrides(key);
  assert.deepEqual(readOverrides(key), {});
});

test('validateSaveBody: accepts insert-op overrides with ins: keys', () => {
  const err = validateSaveBody({
    key: 'test_k',
    overrides: {
      'ins:0': { insert: { targetId: 'BODY0>P0', op: 'before', tag: 'H2', text: 'x', attrs: {} } }
    }
  });
  assert.equal(err, null);
});

// section block round-trip (S8)
test('section block insert: save -> load -> reset round trip (html payload)', () => {
  const key = 'test_section_' + Date.now();
  const sectionHtml = '<nav data-uieditor-id="ins:0" style="display:flex;"><a data-uieditor-id="ins:1" href="#">Home</a></nav>';
  const data = {
    'ins:0': { insert: { targetId: 'HTML0>BODY0>DIV0', op: 'after', html: sectionHtml } }
  };
  writeOverrides(key, data);
  const loaded = readOverrides(key);
  assert.deepEqual(loaded, data);
  assert.equal(loaded['ins:0'].insert.html, sectionHtml);
  resetOverrides(key);
  assert.deepEqual(readOverrides(key), {});
});

test('validateSaveBody: accepts section block overrides with html payload', () => {
  const err = validateSaveBody({
    key: 'test_k',
    overrides: {
      'ins:0': { insert: { targetId: 'BODY0>DIV0', op: 'after', html: '<nav data-uieditor-id="ins:0"><a data-uieditor-id="ins:1">Home</a></nav>' } }
    }
  });
  assert.equal(err, null);
});
