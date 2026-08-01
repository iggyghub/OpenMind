'use strict';

const { VisualiserState } = require('../lib/visualiser-state');

// ── Tracer bullet ─────────────────────────────────────────────────────────────

test('starts in passive state', () => {
  const vs = new VisualiserState();
  expect(vs.state).toBe('passive');
});

// ── Initial conditions ────────────────────────────────────────────────────────

test('starts not visible', () => {
  const vs = new VisualiserState();
  expect(vs.visible).toBe(false);
});

// ── State transitions via handleEvent ─────────────────────────────────────────

describe('state transitions', () => {
  test('wake event → active', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'wake' });
    expect(vs.state).toBe('active');
  });

  test('passive event → passive', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'wake' });
    vs.handleEvent({ type: 'passive' });
    expect(vs.state).toBe('passive');
  });

  test('tts_speaking event → speaking', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'tts_speaking', data: { text: 'hello' } });
    expect(vs.state).toBe('speaking');
  });

  test('tts_done event → passive', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'tts_speaking' });
    vs.handleEvent({ type: 'tts_done' });
    expect(vs.state).toBe('passive');
  });

  test('thinking event → thinking', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'wake' });
    vs.handleEvent({ type: 'thinking' });
    expect(vs.state).toBe('thinking');
  });

  test('passive event resets thinking state', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'thinking' });
    vs.handleEvent({ type: 'passive' });
    expect(vs.state).toBe('passive');
  });

  test('model_switching event → thinking', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'model_switching', data: { model_id: 'claude/haiku' } });
    expect(vs.state).toBe('thinking');
  });
});

// ── handleEvent return value ───────────────────────────────────────────────────

describe('handleEvent return value', () => {
  test('returns current state after transition', () => {
    const vs = new VisualiserState();
    const result = vs.handleEvent({ type: 'wake' });
    expect(result.state).toBe('active');
  });

  test('returns current visibility', () => {
    const vs = new VisualiserState();
    const result = vs.handleEvent({ type: 'wake' });
    expect(result.visible).toBe(false);
  });

  test('returns changed:true when state changes', () => {
    const vs = new VisualiserState();
    const result = vs.handleEvent({ type: 'wake' });
    expect(result.changed).toBe(true);
  });

  test('returns changed:false when state stays same', () => {
    const vs = new VisualiserState();
    // Already passive — firing passive again is a no-op
    const result = vs.handleEvent({ type: 'passive' });
    expect(result.changed).toBe(false);
  });

  test('returns changed:false for unknown event types', () => {
    const vs = new VisualiserState();
    const result = vs.handleEvent({ type: 'heartbeat', data: {} });
    expect(result.changed).toBe(false);
  });
});

// ── toggle ─────────────────────────────────────────────────────────────────────

describe('toggle', () => {
  test('first toggle makes visible', () => {
    const vs = new VisualiserState();
    vs.toggle();
    expect(vs.visible).toBe(true);
  });

  test('second toggle hides', () => {
    const vs = new VisualiserState();
    vs.toggle();
    vs.toggle();
    expect(vs.visible).toBe(false);
  });

  test('toggle returns new visibility', () => {
    const vs = new VisualiserState();
    const result = vs.toggle();
    expect(result.visible).toBe(true);
  });

  test('toggle does not change state', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'wake' });
    vs.toggle();
    expect(vs.state).toBe('active');
  });
});

// ── change events ──────────────────────────────────────────────────────────────

describe('change events', () => {
  test('emits change on state transition', () => {
    const vs = new VisualiserState();
    const calls = [];
    vs.on('change', (payload) => calls.push(payload));
    vs.handleEvent({ type: 'wake' });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ state: 'active', visible: false, driving: false });
  });

  test('does not emit change when state stays same', () => {
    const vs = new VisualiserState();
    const calls = [];
    vs.on('change', () => calls.push(1));
    vs.handleEvent({ type: 'passive' }); // already passive
    expect(calls).toHaveLength(0);
  });

  test('emits change on toggle', () => {
    const vs = new VisualiserState();
    const calls = [];
    vs.on('change', (payload) => calls.push(payload));
    vs.toggle();
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ state: 'passive', visible: true, driving: false });
  });
});

// ── initialState option ────────────────────────────────────────────────────────

test('accepts custom initialState for testing', () => {
  const vs = new VisualiserState({ initialState: 'active' });
  expect(vs.state).toBe('active');
});

test('accepts initialVisible:true for testing', () => {
  const vs = new VisualiserState({ initialVisible: true });
  expect(vs.visible).toBe(true);
});

// ── S2 #576 (ADR-0016 (c)) — Felix-is-driving overlay ────────────────────────

describe('computer_use:driving overlay', () => {
  test('starts not driving', () => {
    const vs = new VisualiserState();
    expect(vs.driving).toBe(false);
  });

  test('flips on for driving:true', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'computer_use:driving', data: { driving: true } });
    expect(vs.driving).toBe(true);
  });

  test('flips off for driving:false', () => {
    const vs = new VisualiserState({ initialDriving: true });
    vs.handleEvent({ type: 'computer_use:driving', data: { driving: false } });
    expect(vs.driving).toBe(false);
  });

  test('does not touch animation state', () => {
    const vs = new VisualiserState();
    vs.handleEvent({ type: 'wake' });
    vs.handleEvent({ type: 'computer_use:driving', data: { driving: true } });
    expect(vs.state).toBe('active');
    expect(vs.driving).toBe(true);
  });

  test('emits change with driving payload on flip', () => {
    const vs = new VisualiserState();
    const calls = [];
    vs.on('change', (p) => calls.push(p));
    vs.handleEvent({ type: 'computer_use:driving', data: { driving: true } });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ state: 'passive', visible: false, driving: true });
  });

  test('does not emit change when driving stays same', () => {
    const vs = new VisualiserState({ initialDriving: true });
    const calls = [];
    vs.on('change', () => calls.push(1));
    vs.handleEvent({ type: 'computer_use:driving', data: { driving: true } });
    expect(calls).toHaveLength(0);
  });

  test('returns changed flag and current driving value', () => {
    const vs = new VisualiserState();
    const r = vs.handleEvent({ type: 'computer_use:driving', data: { driving: true } });
    expect(r).toEqual({
      state: 'passive', visible: false, driving: true, changed: true,
    });
  });
});
