'use strict';

// Tests for tray/lib/workspace.js (S2 -- #481).
// Covers open/close/activate + localStorage persistence.
// DOM wiring lives in main.html and is verified by the human eye-only checklist
// in docs/harness-ui-live-verify.md.

const Workspace = require('../lib/workspace');

function makeMockStorage() {
  const store = {};
  return {
    _store:     store,
    getItem:    (k)    => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem:    (k, v) => { store[k] = String(v); },
    removeItem: (k)    => { delete store[k]; },
  };
}

beforeEach(() => { global.localStorage = makeMockStorage(); });
afterEach(()  => { delete global.localStorage; });

// ── readState ────────────────────────────────────────────────────────────────

describe('readState', () => {
  test('returns empty state when nothing is stored', () => {
    expect(Workspace.readState()).toEqual({ open: [], active: null });
  });

  test('repairs an active id that is not in the open list', () => {
    localStorage.setItem(Workspace.STORAGE_KEY_OPEN,   JSON.stringify(['a', 'b']));
    localStorage.setItem(Workspace.STORAGE_KEY_ACTIVE, 'ghost');
    expect(Workspace.readState()).toEqual({ open: ['a', 'b'], active: 'a' });
  });

  test('drops duplicates in the persisted open list', () => {
    localStorage.setItem(Workspace.STORAGE_KEY_OPEN, JSON.stringify(['a', 'a', 'b']));
    expect(Workspace.readState().open).toEqual(['a', 'b']);
  });

  test('ignores corrupt JSON in the open key', () => {
    localStorage.setItem(Workspace.STORAGE_KEY_OPEN, '{not-json');
    expect(Workspace.readState()).toEqual({ open: [], active: null });
  });
});

// ── open ─────────────────────────────────────────────────────────────────────

describe('open', () => {
  test('opens a panel, activates it, persists both keys', () => {
    const state = Workspace.open('documents');
    expect(state).toEqual({ open: ['documents'], active: 'documents' });
    expect(Workspace.readState()).toEqual({ open: ['documents'], active: 'documents' });
    expect(localStorage.getItem(Workspace.STORAGE_KEY_OPEN))
      .toBe(JSON.stringify(['documents']));
    expect(localStorage.getItem(Workspace.STORAGE_KEY_ACTIVE)).toBe('documents');
  });

  test('opening a second panel appends and activates it', () => {
    Workspace.open('documents');
    const state = Workspace.open('job-search');
    expect(state.open).toEqual(['documents', 'job-search']);
    expect(state.active).toBe('job-search');
  });

  test('opening an already-open panel just activates it (no duplicate)', () => {
    Workspace.open('documents');
    Workspace.open('job-search');
    const state = Workspace.open('documents');
    expect(state.open).toEqual(['documents', 'job-search']);
    expect(state.active).toBe('documents');
  });

  test('empty id is a no-op', () => {
    const state = Workspace.open('');
    expect(state).toEqual({ open: [], active: null });
  });
});

// ── close ────────────────────────────────────────────────────────────────────

describe('close', () => {
  test('closing the only open panel empties the state', () => {
    Workspace.open('documents');
    const state = Workspace.close('documents');
    expect(state).toEqual({ open: [], active: null });
    // Persistence keys cleared, not just set empty.
    expect(localStorage.getItem(Workspace.STORAGE_KEY_OPEN)).toBeNull();
    expect(localStorage.getItem(Workspace.STORAGE_KEY_ACTIVE)).toBeNull();
  });

  test('closing the active tab activates its left neighbour', () => {
    Workspace.open('documents');
    Workspace.open('job-search');   // active = job-search
    const state = Workspace.close('job-search');
    expect(state).toEqual({ open: ['documents'], active: 'documents' });
  });

  test('closing an inactive tab leaves the active tab alone', () => {
    Workspace.open('documents');
    Workspace.open('job-search');   // active = job-search
    const state = Workspace.close('documents');
    expect(state).toEqual({ open: ['job-search'], active: 'job-search' });
  });

  test('closing a non-open panel is a no-op', () => {
    Workspace.open('documents');
    const state = Workspace.close('ghost');
    expect(state).toEqual({ open: ['documents'], active: 'documents' });
  });
});

// ── activate ─────────────────────────────────────────────────────────────────

describe('activate', () => {
  test('activates an already-open panel and persists', () => {
    Workspace.open('documents');
    Workspace.open('job-search');
    const state = Workspace.activate('documents');
    expect(state.active).toBe('documents');
    expect(localStorage.getItem(Workspace.STORAGE_KEY_ACTIVE)).toBe('documents');
  });

  test('activating a not-yet-open panel opens it', () => {
    const state = Workspace.activate('documents');
    expect(state).toEqual({ open: ['documents'], active: 'documents' });
  });
});

// ── persist across a fresh readState (simulates a reload) ────────────────────

describe('persistence across reload', () => {
  test('open panels + active tab survive a readState round-trip', () => {
    Workspace.open('documents');
    Workspace.open('job-search');
    Workspace.activate('documents');
    // A fresh readState() is what the renderer does on reload.
    expect(Workspace.readState()).toEqual({
      open: ['documents', 'job-search'],
      active: 'documents',
    });
  });

  test('isOpen reflects the persisted state', () => {
    expect(Workspace.isOpen('documents')).toBe(false);
    Workspace.open('documents');
    expect(Workspace.isOpen('documents')).toBe(true);
    Workspace.close('documents');
    expect(Workspace.isOpen('documents')).toBe(false);
  });
});
