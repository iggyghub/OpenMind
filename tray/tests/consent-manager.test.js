'use strict';

// Tray-side consent manager tests — Issue #48.
//
// The ConsentManager is the JS half of the consent surface:
//   1. consumes `consent_request` events from Cerebral
//   2. asks the (injected) UI layer to render a prompt
//   3. routes the user's choice back to Cerebral as `consent_response`
//   4. tracks pending requests so a disconnect / stale response is safe

const { ConsentManager, VALID_CHOICES } = require('../lib/consent-manager');

function fixturePayload(overrides = {}) {
  return {
    request_id:              'req-abc',
    tool_name:               'files.write_journal',
    capability:              'fs_write',
    capability_label:        'Write files on disk',
    capability_description:  'Felix needs to create or modify a file.',
    args_preview:            { path: '/Users/me/journal.md' },
    flags:                   { passive: false, irreversible: false },
    ...overrides,
  };
}

let send, openPrompt, closePrompt, manager;

beforeEach(() => {
  send        = jest.fn();
  openPrompt  = jest.fn();
  closePrompt = jest.fn();
  manager = new ConsentManager({ send, openPrompt, closePrompt });
});

// ── Cycle 1: vocabulary ──────────────────────────────────────────────────────

test('VALID_CHOICES contains exactly the four buttons', () => {
  expect(Array.from(VALID_CHOICES).sort()).toEqual(
    ['deny', 'once', 'persistent', 'session'],
  );
});

// ── Cycle 2: tracer — a single request opens a prompt ────────────────────────

test('handleConsentRequest opens a prompt with the payload', () => {
  const payload = fixturePayload();
  manager.handleConsentRequest(payload);
  expect(openPrompt).toHaveBeenCalledTimes(1);
  expect(openPrompt.mock.calls[0][0].request_id).toBe('req-abc');
  expect(manager.pendingCount).toBe(1);
});

test('handleConsentRequest passes the full record to openPrompt', () => {
  manager.handleConsentRequest(fixturePayload());
  const record = openPrompt.mock.calls[0][0];
  expect(record.tool_name).toBe('files.write_journal');
  expect(record.capability).toBe('fs_write');
  expect(record.capability_label).toBe('Write files on disk');
  expect(record.capability_description).toMatch(/Felix/);
  expect(record.args_preview).toEqual({ path: '/Users/me/journal.md' });
  expect(record.flags).toEqual({ passive: false, irreversible: false });
});

// ── Cycle 3: respond emits consent_response and clears state ─────────────────

test('respond emits a consent_response back to Cerebral', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.respond('req-abc', 'persistent');
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0]).toEqual({
    type: 'consent_response',
    data: { request_id: 'req-abc', choice: 'persistent' },
  });
});

test('respond clears the pending record', () => {
  manager.handleConsentRequest(fixturePayload());
  expect(manager.pendingCount).toBe(1);
  manager.respond('req-abc', 'once');
  expect(manager.pendingCount).toBe(0);
});

test('respond closes the prompt window', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.respond('req-abc', 'session');
  expect(closePrompt).toHaveBeenCalledWith('req-abc');
});

// ── Cycle 4: all four buttons send the right string ─────────────────────────

test.each(['once', 'session', 'persistent', 'deny'])(
  'choice "%s" round-trips verbatim',
  (choice) => {
    manager.handleConsentRequest(fixturePayload());
    manager.respond('req-abc', choice);
    expect(send.mock.calls[0][0].data.choice).toBe(choice);
  },
);

// ── Cycle 5: defensive defaults ──────────────────────────────────────────────

test('unknown choice coerces to deny so we never accidentally grant', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.respond('req-abc', 'yes');
  expect(send.mock.calls[0][0].data.choice).toBe('deny');
});

test('respond to a stale request_id is a no-op (returns false)', () => {
  const ok = manager.respond('never-existed', 'persistent');
  expect(ok).toBe(false);
  expect(send).not.toHaveBeenCalled();
  expect(closePrompt).not.toHaveBeenCalled();
});

test('respond after the request was already resolved is a no-op', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.respond('req-abc', 'persistent');
  send.mockClear();
  closePrompt.mockClear();
  const ok = manager.respond('req-abc', 'session');
  expect(ok).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

// ── Cycle 6: payload validation — malformed events don't crash ──────────────

test('handleConsentRequest ignores null/undefined', () => {
  manager.handleConsentRequest(undefined);
  manager.handleConsentRequest(null);
  expect(openPrompt).not.toHaveBeenCalled();
  expect(manager.pendingCount).toBe(0);
});

test('handleConsentRequest ignores missing request_id', () => {
  manager.handleConsentRequest({ capability: 'fs_write' });
  expect(openPrompt).not.toHaveBeenCalled();
});

test('handleConsentRequest ignores missing capability', () => {
  manager.handleConsentRequest({ request_id: 'r1' });
  expect(openPrompt).not.toHaveBeenCalled();
});

test('handleConsentRequest tolerates missing optional fields', () => {
  manager.handleConsentRequest({ request_id: 'r1', capability: 'fs_write' });
  const record = openPrompt.mock.calls[0][0];
  expect(record.tool_name).toBe('');
  expect(record.capability_label).toBe('fs_write'); // falls back to enum
  expect(record.capability_description).toBe('');
  expect(record.args_preview).toEqual({});
  expect(record.flags).toEqual({ passive: false, irreversible: false });
});

// ── Cycle 7: concurrent requests — per request_id ────────────────────────────

test('two concurrent requests both open prompts', () => {
  manager.handleConsentRequest(fixturePayload({ request_id: 'a', capability: 'fs_write' }));
  manager.handleConsentRequest(fixturePayload({ request_id: 'b', capability: 'screen_capture' }));
  expect(openPrompt).toHaveBeenCalledTimes(2);
  expect(manager.pendingCount).toBe(2);
});

test('respond to one of two open prompts only closes that one', () => {
  manager.handleConsentRequest(fixturePayload({ request_id: 'a' }));
  manager.handleConsentRequest(fixturePayload({ request_id: 'b', capability: 'fs_read' }));
  manager.respond('a', 'session');
  expect(closePrompt).toHaveBeenCalledTimes(1);
  expect(closePrompt).toHaveBeenCalledWith('a');
  expect(manager.pendingCount).toBe(1);
  expect(manager.get('b')).toBeDefined();
});

// ── Cycle 8: reset on disconnect ─────────────────────────────────────────────

test('reset closes all open prompts and clears state', () => {
  manager.handleConsentRequest(fixturePayload({ request_id: 'a' }));
  manager.handleConsentRequest(fixturePayload({ request_id: 'b', capability: 'fs_read' }));
  manager.reset();
  expect(closePrompt).toHaveBeenCalledTimes(2);
  expect(manager.pendingCount).toBe(0);
});

test('reset does NOT emit consent_response — Cerebral will time out on its side', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.reset();
  expect(send).not.toHaveBeenCalled();
});

test('after reset a fresh request still works', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.reset();
  manager.handleConsentRequest(fixturePayload({ request_id: 'fresh' }));
  expect(openPrompt).toHaveBeenLastCalledWith(
    expect.objectContaining({ request_id: 'fresh' }),
  );
  manager.respond('fresh', 'persistent');
  expect(send).toHaveBeenCalledTimes(1);
});

// ── Cycle 9: get() accessor (used by main.js to re-deliver after IPC race) ──

test('get returns the record for a pending request', () => {
  manager.handleConsentRequest(fixturePayload({ request_id: 'q' }));
  expect(manager.get('q').capability).toBe('fs_write');
});

test('get returns undefined for an unknown request_id', () => {
  expect(manager.get('nope')).toBeUndefined();
});

test('get returns undefined after the request is resolved', () => {
  manager.handleConsentRequest(fixturePayload());
  manager.respond('req-abc', 'deny');
  expect(manager.get('req-abc')).toBeUndefined();
});

// ── Cycle 10: pendingCount tracks open prompts ──────────────────────────────

test('pendingCount is zero before any request', () => {
  expect(manager.pendingCount).toBe(0);
});

test('pendingCount counts up and down across requests', () => {
  manager.handleConsentRequest(fixturePayload({ request_id: 'a' }));
  manager.handleConsentRequest(fixturePayload({ request_id: 'b' }));
  manager.handleConsentRequest(fixturePayload({ request_id: 'c' }));
  expect(manager.pendingCount).toBe(3);
  manager.respond('b', 'once');
  expect(manager.pendingCount).toBe(2);
  manager.reset();
  expect(manager.pendingCount).toBe(0);
});
