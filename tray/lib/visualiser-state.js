'use strict';

const { EventEmitter } = require('events');

const STATE_MAP = {
  wake:        'active',
  passive:     'passive',
  tts_speaking: 'speaking',
  tts_done:    'passive',
  thinking:    'thinking',
};

class VisualiserState extends EventEmitter {
  constructor({ initialState = 'passive', initialVisible = false } = {}) {
    super();
    this._state   = initialState;
    this._visible = initialVisible;
  }

  get state()   { return this._state; }
  get visible() { return this._visible; }

  handleEvent(event) {
    const prev = this._state;
    const next = STATE_MAP[event.type];

    if (next !== undefined) this._state = next;

    const changed = this._state !== prev;
    if (changed) {
      this.emit('change', { state: this._state, visible: this._visible });
    }

    return { state: this._state, visible: this._visible, changed };
  }

  toggle() {
    this._visible = !this._visible;
    this.emit('change', { state: this._state, visible: this._visible });
    return { visible: this._visible };
  }
}

module.exports = { VisualiserState };
