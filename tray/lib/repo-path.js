'use strict';

// Pure path-containment check: is `absPath` inside `root`, expressed as a
// forward-slash repo-relative string? Used by main.js's UI Editor Save/Open
// dialog IPC handlers to reject a pick outside the repo before it ever
// reaches tools/ui-editor/server.js's own (separate) containment check.
//
// Dual-mode: window.RepoPath in a renderer (unused today, but every other
// tray/lib/*.js module follows this shape), module.exports for Node/jest and
// for main.js's own require().

const path = require('path');

function relativeToRoot(root, absPath) {
  const rel = path.relative(root, absPath);
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) return null;
  return rel.split(path.sep).join('/');
}

const _exports = { relativeToRoot: relativeToRoot };

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.RepoPath = _exports;
}
