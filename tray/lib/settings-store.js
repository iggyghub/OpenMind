'use strict';

const fs = require('fs');

class SettingsStore {
  constructor(filePath, defaults = {}) {
    this._filePath = filePath;
    this._defaults = defaults;
    this._data = this._load();
  }

  _load() {
    try {
      return { ...this._defaults, ...JSON.parse(fs.readFileSync(this._filePath, 'utf-8')) };
    } catch {
      return { ...this._defaults };
    }
  }

  get(key) {
    return key in this._data ? this._data[key] : this._defaults[key];
  }

  set(key, value) {
    this._data[key] = value;
    try {
      fs.writeFileSync(this._filePath, JSON.stringify(this._data), 'utf-8');
    } catch { /* silently ignore — disk errors shouldn't crash the app */ }
  }
}

module.exports = { SettingsStore };
