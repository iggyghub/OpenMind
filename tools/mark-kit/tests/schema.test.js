'use strict';

// mark.schema.json must describe every field the page writes, so a fixer (or
// another AI integrating mark-kit) can trust it.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const schema = JSON.parse(fs.readFileSync(path.join(root, 'mark.schema.json'), 'utf8'));
const src = fs.readFileSync(path.join(root, 'mark-kit.js'), 'utf8');

test('every key the page puts on a mark is in the schema', () => {
  const literal = src.match(/var extra = \{([\s\S]*?)\n\s*\};/)[1];
  const keys = new Set([
    ...[...literal.matchAll(/^\s*(\w+):/gm)].map((m) => m[1]),
    ...[...src.matchAll(/extra\.(\w+) =/g)].map((m) => m[1]),
    ...Object.keys(require('../mark-kit').describe(
      { tagName: 'DIV', id: 'x', classList: [], textContent: '', parentElement: null, getAttribute: () => null },
      { getPropertyValue: () => '' }, 'n')),
  ]);
  const missing = [...keys].filter((k) => !(k in schema.properties));
  assert.deepStrictEqual(missing, []);
  assert.ok(keys.size > 15, 'regexes still find the fields');
});

test('the schema types and lifecycle match the code', () => {
  assert.deepStrictEqual(schema.properties.type.enum, require('../mark-kit').TYPES);
  assert.deepStrictEqual(schema.properties.status.enum, ['open', 'fixing', 'fixed', 'verified', 'reopened', 'declined']);
});
