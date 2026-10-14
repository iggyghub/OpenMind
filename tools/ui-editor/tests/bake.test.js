'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const os = require('os');
const fs = require('fs');
const path = require('path');

const { bake, findByPath, findByIdAttr, mergeStyleAttr, mergeAttrsIntoTag } = require('../html-bake.js');

// ---- mergeStyleAttr ----

test('mergeStyleAttr: adds style attr when absent', () => {
  const out = mergeStyleAttr('<p>', { color: '#ff0000' });
  assert.match(out, /style="color:#ff0000"/);
});

test('mergeStyleAttr: merges into existing style, preserves unrelated props', () => {
  const out = mergeStyleAttr('<p style="font-size:14px">', { color: '#ff0000' });
  assert.match(out, /font-size:14px/);
  assert.match(out, /color:#ff0000/);
});

test('mergeStyleAttr: camelCase to kebab-case conversion', () => {
  const out = mergeStyleAttr('<div>', { backgroundColor: '#abc', marginLeft: '10px' });
  assert.match(out, /background-color:#abc/);
  assert.match(out, /margin-left:10px/);
});

test('mergeStyleAttr: overrides existing same prop', () => {
  const out = mergeStyleAttr('<span style="color:red">', { color: '#00ff00' });
  assert.doesNotMatch(out, /color:red/);
  assert.match(out, /color:#00ff00/);
});

// ---- findByPath ----

test('findByPath: finds body element', () => {
  const html = '<html><head></head><body><p>hi</p></body></html>';
  const pos = findByPath(html, 'BODY1');
  assert.ok(pos, 'should find BODY');
  assert.equal(html.slice(pos.openStart, pos.openStart + 6), '<body>');
});

test('findByPath: finds nested element', () => {
  const html = '<html><head></head><body><p id="x">hi</p></body></html>';
  const pos = findByPath(html, 'BODY1>P0');
  assert.ok(pos, 'should find P inside BODY');
  assert.match(html.slice(pos.openStart, pos.openEnd), /id="x"/);
});

test('findByPath: returns null for stale path (wrong tag at index)', () => {
  const html = '<html><head></head><body><div></div></body></html>';
  const pos = findByPath(html, 'BODY1>P0'); // P0 but only DIV there
  assert.equal(pos, null);
});

test('findByPath: returns null for out-of-range index', () => {
  const html = '<html><head></head><body><p>one</p></body></html>';
  assert.equal(findByPath(html, 'BODY1>P1'), null); // only P0 exists
});

test('findByPath: skips comments correctly', () => {
  const html = '<html><head></head><body><!-- comment --><p>x</p></body></html>';
  const pos = findByPath(html, 'BODY1>P0');
  assert.ok(pos);
  assert.match(html.slice(pos.openStart, pos.openEnd), /<p>/);
});

test('findByPath: handles sibling elements at correct indices', () => {
  const html = '<html><head></head><body><div></div><p>target</p></body></html>';
  const pos = findByPath(html, 'BODY1>P1'); // P is at child index 1
  assert.ok(pos);
  assert.match(html.slice(pos.openStart, pos.openEnd), /<p>/);
});

// ---- bake (main function) ----

test('bake: applies style override to matching element', () => {
  const html = '<html><head></head><body><p>hello</p></body></html>';
  const overrides = { 'BODY1>P0': { style: { color: '#ff0000' } } };
  const out = bake(html, overrides);
  assert.match(out, /<p style="color:#ff0000">hello<\/p>/);
});

test('bake: applies text override to matching element', () => {
  const html = '<html><head></head><body><p>original</p></body></html>';
  const overrides = { 'BODY1>P0': { text: 'updated' } };
  const out = bake(html, overrides);
  assert.match(out, /<p>updated<\/p>/);
});

test('bake: applies both style and text override to same element', () => {
  const html = '<html><head></head><body><p>old</p></body></html>';
  const overrides = { 'BODY1>P0': { style: { color: '#0000ff' }, text: 'new' } };
  const out = bake(html, overrides);
  assert.match(out, /<p style="color:#0000ff">new<\/p>/);
});

test('bake: escapes HTML in text override', () => {
  const html = '<html><head></head><body><p>x</p></body></html>';
  const overrides = { 'BODY1>P0': { text: '<script>alert(1)</script>' } };
  const out = bake(html, overrides);
  assert.match(out, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(out, /<script>alert/);
});

test('bake: multiple sibling overrides applied independently', () => {
  const html = '<html><head></head><body><div>title</div><p>body</p></body></html>';
  const overrides = {
    'BODY1>DIV0': { style: { color: 'red' } },
    'BODY1>P1': { text: 'changed' },
  };
  const out = bake(html, overrides);
  assert.match(out, /<div style="color:red">title<\/div>/);
  assert.match(out, /<p>changed<\/p>/);
});

test('bake: does not disturb unrelated markup', () => {
  const html = '<html><head><title>T</title></head><body><p>hi</p><footer>f</footer></body></html>';
  const overrides = { 'BODY1>P0': { style: { color: 'blue' } } };
  const out = bake(html, overrides);
  assert.match(out, /<title>T<\/title>/);
  assert.match(out, /<footer>f<\/footer>/);
});

test('bake: no-op for unknown path', () => {
  const html = '<html><head></head><body><p>hi</p></body></html>';
  const overrides = { 'BODY1>DIV0': { style: { color: 'red' } } };
  const out = bake(html, overrides);
  assert.equal(out, html); // unchanged
});

// ---- mergeAttrsIntoTag ----

test('mergeAttrsIntoTag: adds attr when absent', () => {
  const out = mergeAttrsIntoTag('<img>', { src: '/assets/a.png' });
  assert.match(out, /src="\/assets\/a\.png"/);
});

test('mergeAttrsIntoTag: overwrites existing attr, escapes quotes', () => {
  const out = mergeAttrsIntoTag('<img src="old.png" alt="x">', { src: 'new "b".png' });
  assert.match(out, /src="new &quot;b&quot;\.png"/);
  assert.match(out, /alt="x"/);
  assert.doesNotMatch(out, /old\.png/);
});

// ---- bake: single-block inserts (S7) ----

test('bake: single-block insert appended as last child', () => {
  const html = '<html><head></head><body><p>hi</p></body></html>';
  const overrides = { 'ins:0': { insert: { targetId: 'BODY1', op: 'append', tag: 'P', text: 'new one' } } };
  const out = bake(html, overrides);
  assert.match(out, /<p>hi<\/p><P data-uieditor-id="ins:0">new one<\/P><\/body>/);
});

test('bake: single-block insert before/after target', () => {
  const html = '<html><head></head><body><p>hi</p></body></html>';
  const overrides = {
    'ins:0': { insert: { targetId: 'BODY1>P0', op: 'before', tag: 'H1', text: 'Heading' } },
    'ins:1': { insert: { targetId: 'BODY1>P0', op: 'after', tag: 'BUTTON', text: 'Go' } },
  };
  const out = bake(html, overrides);
  assert.match(out, /<H1[^>]*>Heading<\/H1><p>hi<\/p><BUTTON[^>]*>Go<\/BUTTON>/);
});

test('bake: inserted element carries its own later style edit', () => {
  const html = '<html><head></head><body></body></html>';
  const overrides = { 'ins:0': { insert: { targetId: 'BODY1', op: 'append', tag: 'P', text: 'styled' }, style: { color: '#ff0000' } } };
  const out = bake(html, overrides);
  assert.match(out, /<P data-uieditor-id="ins:0" style="color:#ff0000">styled<\/P>/);
});

test('bake: void-tag insert (img) has no closing tag or text', () => {
  const html = '<html><head></head><body></body></html>';
  const overrides = { 'ins:0': { insert: { targetId: 'BODY1', op: 'append', tag: 'img', attrs: { src: 'placeholder.png', alt: 'placeholder' } } } };
  const out = bake(html, overrides);
  assert.match(out, /<img data-uieditor-id="ins:0" src="placeholder\.png" alt="placeholder">/);
});

// ---- bake: section-block inserts (S8) ----

test('bake: section-block insert splices stored outerHTML verbatim', () => {
  const html = '<html><head></head><body></body></html>';
  const sectionHtml = '<nav data-uieditor-id="ins:0"><a data-uieditor-id="ins:1">Home</a></nav>';
  const overrides = { 'ins:0': { insert: { targetId: 'BODY1', op: 'append', html: sectionHtml } } };
  const out = bake(html, overrides);
  assert.match(out, /<nav data-uieditor-id="ins:0"><a data-uieditor-id="ins:1">Home<\/a><\/nav><\/body>/);
});

test('bake: edit to a child inside an already-inserted section is applied via its id attr', () => {
  const html = '<html><head></head><body></body></html>';
  const sectionHtml = '<nav data-uieditor-id="ins:0"><a data-uieditor-id="ins:1">Home</a></nav>';
  const overrides = {
    'ins:0': { insert: { targetId: 'BODY1', op: 'append', html: sectionHtml } },
    'ins:1': { text: 'Homepage', style: { color: 'blue' } },
  };
  const out = bake(html, overrides);
  assert.match(out, /<a data-uieditor-id="ins:1" style="color:blue">Homepage<\/a>/);
});

test('findByIdAttr: locates a previously-baked inserted element by its id attribute', () => {
  const html = '<body><div><span data-uieditor-id="ins:2">hi</span></div></body>';
  const pos = findByIdAttr(html, 'ins:2');
  assert.ok(pos);
  assert.equal(html.slice(pos.openStart, pos.closeEnd), '<span data-uieditor-id="ins:2">hi</span>');
});

// ---- bake: attrs on existing elements (S11 image replace) ----

test('bake: attrs override on an existing element (e.g. image src replace)', () => {
  const html = '<html><head></head><body><img src="old.png"></body></html>';
  const overrides = { 'BODY1>IMG0': { attrs: { src: '/assets/k/new.png' } } };
  const out = bake(html, overrides);
  assert.match(out, /<img src="\/assets\/k\/new\.png">/);
});

// ---- bake: bp-scoped keys are skipped (no JS in a static file to switch bands) ----

test('bake: bp-scoped style override is skipped, unscoped one for the same id still applies', () => {
  const html = '<html><head></head><body><p>hi</p></body></html>';
  const overrides = {
    'BODY1>P0': { style: { color: 'black' } },
    'BODY1>P0|mobile': { style: { color: 'red' }, bp: 'mobile' },
  };
  const out = bake(html, overrides);
  assert.match(out, /color:black/);
  assert.doesNotMatch(out, /color:red/);
});

// ---- file round-trip via server bake handler ----

test('round-trip: save overrides then bake then re-read verifies inline style+text', () => {
  const { writeOverrides, sanitizeKey } = require('../server.js');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'uieditor-'));
  const fixture = path.join(tmpDir, 'page.html');
  const original = [
    '<html><head></head><body>',
    '<section id="hdr">Hello</section>',
    '<p class="intro">World</p>',
    '</body></html>',
  ].join('\n');
  fs.writeFileSync(fixture, original, 'utf8');

  const key = sanitizeKey('local:test/page.html');
  const overrides = {
    'BODY1>SECTION0': { style: { color: '#ff0000', fontSize: '32px' } },
    'BODY1>P1': { text: 'baked text', style: { backgroundColor: '#eee' } },
  };
  writeOverrides(key, overrides);

  const baked = bake(fs.readFileSync(fixture, 'utf8'), overrides);
  fs.writeFileSync(fixture, baked, 'utf8');

  const result = fs.readFileSync(fixture, 'utf8');
  assert.match(result, /color:#ff0000/);
  assert.match(result, /font-size:32px/);
  assert.match(result, /<p[^>]*background-color:#eee[^>]*>baked text<\/p>/);
  assert.match(result, /id="hdr"/);           // original attribute preserved
  assert.match(result, /class="intro"/);      // original attribute preserved
  assert.doesNotMatch(result, /World/);       // original text replaced

  // cleanup
  fs.rmSync(tmpDir, { recursive: true });
  const { resetOverrides } = require('../server.js');
  resetOverrides(key);
});
