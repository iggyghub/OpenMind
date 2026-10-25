'use strict';

// mark-kit must stay host-agnostic so it can ship on its own: no imports, and
// no knowledge of Felix (or any other host) baked into the core.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '..', 'mark-kit.js'), 'utf8');

test('no require/import at all', () => {
  assert.doesNotMatch(src, /\brequire\s*\(|^\s*import\s|import\s*\(/m);
});

test('no host names or host-only hooks', () => {
  for (const word of ['tray', 'cerebral', 'felix', 'data-route', 'sendEvent', 'ipcRenderer']) {
    assert.ok(!src.toLowerCase().includes(word.toLowerCase()), `mark-kit.js mentions "${word}"`);
  }
});
