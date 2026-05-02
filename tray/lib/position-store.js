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

module.exports = { PositionStore };
