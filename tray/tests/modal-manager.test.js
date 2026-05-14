'use strict';

// Tray-side irreversible-modal manager tests — Issue #49.
//
// The ModalManager is the JS half of the irreversible-flag modal:
//   1. consumes `irreversible_modal_request` events from Cerebral
//   2. asks the (injected) UI layer to render a two-button modal
//   3. routes the user's Accept / Cancel back to Cerebral as
//      `irreversible_modal_response`
//   4. tracks pending requests so a disconnect / stale response is safe
//
// Mirrors consent-manager.test.js's slice structure but with a strictly
// two-choice vocabulary — no Once/Session/Persistent, no ACL mutation,
// no per-class lock. Unknown choices (including the consent surface's
// four verbs) coerce to Cancel so an irreversible can never accidentally
// dispatch on a malformed message.

const { ModalManager, VALID_MODAL_CHOICES } = require('../lib/modal-manager');

function fixturePayload(overrides = {}) {
  return {
    request_id:              'modal-abc',
    tool_name:               'files.delete',
    capability:              'fs_delete',
    capability_label:        'Delete files on disk',
    capability_description:  'Felix needs to delete a file. This cannot be undone.',
    args_preview:            { path: '/Users/me/notes.md' },
    flags:                   { passive: false, irreversible: true },
    ...overrides,
  };
}

let send, openPrompt, closePrompt, manager;

beforeEach(() => {
  send        = jest.fn();
  openPrompt  = jest.fn();
  closePrompt = jest.fn();
  manager = new ModalManager({ send, openPrompt, closePrompt });
});

// ── Cycle 1: vocabulary ──────────────────────────────────────────────────────

test('VALID_MODAL_CHOICES contains exactly accept and cancel', () => {
  expect(Array.from(VALID_MODAL_CHOICES).sort()).toEqual(['accept', 'cancel']);
});

// ── Cycle 2: tracer — a request opens a prompt ────────────────────────────────

test('handleModalRequest opens a prompt with the payload', () => {
  manager.handleModalRequest(fixturePayload());
  expect(openPrompt).toHaveBeenCalledTimes(1);
  expect(openPrompt.mock.calls[0][0].request_id).toBe('modal-abc');
  expect(manager.pendingCount).toBe(1);
});

test('handleModalRequest passes the full record to openPrompt', () => {
  manager.handleModalRequest(fixturePayload());
  const record = openPrompt.mock.calls[0][0];
  expect(record.tool_name).toBe('files.delete');
  expect(record.capability).toBe('fs_delete');
  expect(record.capability_label).toBe('Delete files on disk');
  expect(record.capability_description).toMatch(/cannot be undone/);
  expect(record.args_preview).toEqual({ path: '/Users/me/notes.md' });
  expect(record.flags).toEqual({ passive: false, irreversible: true });
});

// ── Cycle 3: respond emits irreversible_modal_response and clears state ──────

test('respond emits an irreversible_modal_response back to Cerebral', () => {
  manager.handleModalRequest(fixturePayload());
  manager.respond('modal-abc', 'accept');
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0]).toEqual({
    type: 'irreversible_modal_response',
    data: { request_id: 'modal-abc', choice: 'accept' },
  });
});

test('respond clears the pending record', () => {
  manager.handleModalRequest(fixturePayload());
  expect(manager.pendingCount).toBe(1);
  manager.respond('modal-abc', 'cancel');
  expect(manager.pendingCount).toBe(0);
});

test('respond closes the prompt window', () => {
  manager.handleModalRequest(fixturePayload());
  manager.respond('modal-abc', 'accept');
  expect(closePrompt).toHaveBeenCalledWith('modal-abc');
});

// ── Cycle 4: both buttons round-trip verbatim ────────────────────────────────

test.each(['accept', 'cancel'])(
  'choice "%s" round-trips verbatim',
  (choice) => {
    manager.handleModalRequest(fixturePayload());
    manager.respond('modal-abc', choice);
    expect(send.mock.calls[0][0].data.choice).toBe(choice);
  },
);

// ── Cycle 5: defensive defaults ──────────────────────────────────────────────

test('unknown choice coerces to cancel so we never accidentally dispatch', () => {
  manager.handleModalRequest(fixturePayload());
  manager.respond('modal-abc', 'yes');
  expect(send.mock.calls[0][0].data.choice).toBe('cancel');
});

test('consent surface verbs coerce to cancel (irreversible has no session/persistent)', () => {
  manager.handleModalRequest(fixturePayload());
  manager.respond('modal-abc', 'persistent');
  expect(send.mock.calls[0][0].data.choice).toBe('cancel');
});

test('respond to a stale request_id is a no-op (returns false)', () => {
  const ok = manager.respond('never-existed', 'accept');
  expect(ok).toBe(false);
  expect(send).not.toHaveBeenCalled();
  expect(closePrompt).not.toHaveBeenCalled();
});

test('respond after the request was already resolved is a no-op', () => {
  manager.handleModalRequest(fixturePayload());
  manager.respond('modal-abc', 'accept');
  send.mockClear();
  closePrompt.mockClear();
  const ok = manager.respond('modal-abc', 'cancel');
  expect(ok).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

// ── Cycle 6: payload validation — malformed events don't crash ──────────────

test('handleModalRequest ignores null/undefined', () => {
  manager.handleModalRequest(undefined);
  manager.handleModalRequest(null);
  expect(openPrompt).not.toHaveBeenCalled();
  expect(manager.pendingCount).toBe(0);
});

test('handleModalRequest ignores missing request_id', () => {
  manager.handleModalRequest({ capability: 'fs_delete' });
  expect(openPrompt).not.toHaveBeenCalled();
});

test('handleModalRequest ignores missing capability', () => {
  manager.handleModalRequest({ request_id: 'r1' });
  expect(openPrompt).not.toHaveBeenCalled();
});

test('handleModalRequest tolerates missing optional fields', () => {
  manager.handleModalRequest({ request_id: 'r1', capability: 'fs_delete' });
  const record = openPrompt.mock.calls[0][0];
  expect(record.tool_name).toBe('');
  expect(record.capability_label).toBe('fs_delete'); // falls back to enum
  expect(record.capability_description).toBe('');
  expect(record.args_preview).toEqual({});
  expect(record.flags).toEqual({ passive: false, irreversible: true });
});

// ── Cycle 7: concurrent requests — per request_id ────────────────────────────

test('two concurrent requests both open prompts', () => {
  manager.handleModalRequest(fixturePayload({ request_id: 'a' }));
  manager.handleModalRequest(fixturePayload({ request_id: 'b', capability: 'shell_exec' }));
  expect(openPrompt).toHaveBeenCalledTimes(2);
  expect(manager.pendingCount).toBe(2);
});

test('respond to one of two open prompts only closes that one', () => {
  manager.handleModalRequest(fixturePayload({ request_id: 'a' }));
  manager.handleModalRequest(fixturePayload({ request_id: 'b' }));
  manager.respond('a', 'accept');
  expect(closePrompt).toHaveBeenCalledTimes(1);
  expect(closePrompt).toHaveBeenCalledWith('a');
  expect(manager.pendingCount).toBe(1);
  expect(manager.get('b')).toBeDefined();
});

// ── Cycle 8: reset on disconnect ─────────────────────────────────────────────

test('reset closes all open prompts and clears state', () => {
  manager.handleModalRequest(fixturePayload({ request_id: 'a' }));
  manager.handleModalRequest(fixturePayload({ request_id: 'b' }));
  manager.reset();
  expect(closePrompt).toHaveBeenCalledTimes(2);
  expect(manager.pendingCount).toBe(0);
});

test('reset does NOT emit a response — Cerebral will time out and DENY', () => {
  manager.handleModalRequest(fixturePayload());
  manager.reset();
  expect(send).not.toHaveBeenCalled();
});

test('after reset a fresh request still works', () => {
  manager.handleModalRequest(fixturePayload());
  manager.reset();
  manager.handleModalRequest(fixturePayload({ request_id: 'fresh' }));
  expect(openPrompt).toHaveBeenLastCalledWith(
    expect.objectContaining({ request_id: 'fresh' }),
  );
  manager.respond('fresh', 'accept');
  expect(send).toHaveBeenCalledTimes(1);
});

// ── Cycle 9: get() accessor (re-deliver after IPC race) ─────────────────────

test('get returns the record for a pending request', () => {
  manager.handleModalRequest(fixturePayload({ request_id: 'q' }));
  expect(manager.get('q').capability).toBe('fs_delete');
});

test('get returns undefined for an unknown request_id', () => {
  expect(manager.get('nope')).toBeUndefined();
});

test('get returns undefined after the request is resolved', () => {
  manager.handleModalRequest(fixturePayload());
  manager.respond('modal-abc', 'cancel');
  expect(manager.get('modal-abc')).toBeUndefined();
});

// ── Cycle 10: pendingCount tracks open prompts ──────────────────────────────

test('pendingCount is zero before any request', () => {
  expect(manager.pendingCount).toBe(0);
});

test('pendingCount counts up and down across requests', () => {
  manager.handleModalRequest(fixturePayload({ request_id: 'a' }));
  manager.handleModalRequest(fixturePayload({ request_id: 'b' }));
  manager.handleModalRequest(fixturePayload({ request_id: 'c' }));
  expect(manager.pendingCount).toBe(3);
  manager.respond('b', 'accept');
  expect(manager.pendingCount).toBe(2);
  manager.reset();
  expect(manager.pendingCount).toBe(0);
});
