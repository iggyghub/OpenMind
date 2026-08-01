'use strict';

const { EventEmitter } = require('events');

const STATE_MAP = {
  wake:             'active',
  passive:          'passive',
  tts_speaking:     'speaking',
  tts_done:         'passive',
  thinking:         'thinking',
  model_switching:  'thinking',
};

class VisualiserState extends EventEmitter {
  constructor({
    initialState   = 'passive',
    initialVisible = false,
    initialDriving = false,
  } = {}) {
    super();
    this._state    = initialState;
    this._visible  = initialVisible;
    // S2 #576 (ADR-0016 (c)): true while computer_use is actuating and the
    // Visualiser must render its Stop control. Independent of the animation
    // state so it can overlay any base state.
    this._driving  = initialDriving;
  }

  get state()   { return this._state; }
  get visible() { return this._visible; }
  get driving() { return this._driving; }

  handleEvent(event) {
    const prev = this._state;

    // computer_use:driving is a boolean overlay, not a state transition. It
    // rides alongside the (wake|passive|speaking|thinking) animation states.
    if (event.type === 'computer_use:driving') {
      const nextDriving = !!(event.data && event.data.driving);
      const changed = nextDriving !== this._driving;
      this._driving = nextDriving;
      if (changed) {
        this.emit('change', {
          state:    this._state,
          visible:  this._visible,
          driving:  this._driving,
        });
      }
      return { state: this._state, visible: this._visible, driving: this._driving, changed };
    }

    const next = STATE_MAP[event.type];
    if (next !== undefined) this._state = next;

    const changed = this._state !== prev;
    if (changed) {
      this.emit('change', {
        state:   this._state,
        visible: this._visible,
        driving: this._driving,
      });
    }

    return { state: this._state, visible: this._visible, driving: this._driving, changed };
  }

  toggle() {
    this._visible = !this._visible;
    this.emit('change', {
      state:   this._state,
      visible: this._visible,
      driving: this._driving,
    });
    return { visible: this._visible };
  }
}

module.exports = { VisualiserState };
