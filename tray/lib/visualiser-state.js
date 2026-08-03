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
    // #594 (ADR-0016 amendment f): mode-aware driving payload -- "background"
    // (no cursor, control pattern) vs "foreground" (pyautogui, cursor-in-use
    // urgency) -- plus the window/action the indicator names. null when not
    // driving (or before the first driving event ever arrives).
    this._mode        = null;
    this._windowTitle = null;
    this._action      = null;
  }

  get state()       { return this._state; }
  get visible()     { return this._visible; }
  get driving()     { return this._driving; }
  get mode()        { return this._mode; }
  get windowTitle() { return this._windowTitle; }
  get action()      { return this._action; }

  _drivingPayload() {
    return {
      state:       this._state,
      visible:     this._visible,
      driving:     this._driving,
      mode:        this._mode,
      windowTitle: this._windowTitle,
      action:      this._action,
    };
  }

  handleEvent(event) {
    const prev = this._state;

    // computer_use:driving is a boolean-plus-mode overlay, not a state
    // transition. It rides alongside the (wake|passive|speaking|thinking)
    // animation states.
    if (event.type === 'computer_use:driving') {
      const data = event.data || {};
      const nextDriving     = !!data.driving;
      const nextMode        = data.mode || null;
      const nextWindowTitle = data.window_title || null;
      const nextAction      = data.action || null;
      const changed = (
        nextDriving !== this._driving
        || nextMode !== this._mode
        || nextWindowTitle !== this._windowTitle
        || nextAction !== this._action
      );
      this._driving     = nextDriving;
      this._mode        = nextMode;
      this._windowTitle = nextWindowTitle;
      this._action      = nextAction;
      if (changed) this.emit('change', this._drivingPayload());
      return { ...this._drivingPayload(), changed };
    }

    const next = STATE_MAP[event.type];
    if (next !== undefined) this._state = next;

    const changed = this._state !== prev;
    if (changed) this.emit('change', this._drivingPayload());

    return { ...this._drivingPayload(), changed };
  }

  toggle() {
    this._visible = !this._visible;
    this.emit('change', this._drivingPayload());
    return { visible: this._visible };
  }
}

module.exports = { VisualiserState };
