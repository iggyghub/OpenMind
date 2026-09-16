'use strict';

const fs = require('fs');

class PositionStore {
  constructor(filePath) {
    this._path = filePath;
  }

  save(position) {
    try {
      fs.writeFileSync(this._path, JSON.stringify(position), 'utf8');
    } catch {
      // best-effort; position persistence must not crash the app
    }
  }

  load() {
    try {
      return JSON.parse(fs.readFileSync(this._path, 'utf8'));
    } catch {
      return null;
    }
  }
}

// #820 -- true if (x, y) falls within any of the given Electron `screen`
// displays. Pure function so "would a saved window position still be
// visible" is testable without a real Electron `screen` module -- pass
// `screen.getAllDisplays()`'s shape ({ bounds: {x,y,width,height} }[]).
function isPointOnAnyDisplay(x, y, displays) {
  return displays.some(({ bounds }) =>
    x >= bounds.x && x < bounds.x + bounds.width
    && y >= bounds.y && y < bounds.y + bounds.height,
  );
}

module.exports = { PositionStore, isPointOnAnyDisplay };
