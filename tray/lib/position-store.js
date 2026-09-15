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

// True if (x, y) falls within some display's bounds. Used to reject a saved
// window position that no longer lands on any connected monitor (e.g. a
// second display was unplugged since the position was saved).
function isPointOnAnyDisplay(point, displays) {
  return displays.some(({ bounds: b }) =>
    point.x >= b.x && point.x < b.x + b.width &&
    point.y >= b.y && point.y < b.y + b.height);
}

module.exports = { PositionStore, isPointOnAnyDisplay };
