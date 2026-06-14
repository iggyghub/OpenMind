'use strict';

// Tray-side consent surface (Issue #48, ADR-0005).
//
// Cerebral emits a `consent_request` event when the per-profile ACL
// resolves to ASK. This manager:
//   1. records the open request keyed by request_id
//   2. asks the UI layer to display a prompt for that request
//   3. routes the user's choice back to Cerebral as `consent_response`
//
// The UI layer is injected as two callbacks (`openPrompt`, `closePrompt`)
// so this class stays UI-free and unit-testable. main.js wires the
// real Electron BrowserWindow lifecycle into those callbacks.
//
// Dual-mode export: the same source feeds the Node test suite (and main.js)
// via require() AND can be loaded into a renderer via a plain <script src>
// tag (the windows run nodeIntegration:false per ADR-0007 so they can't
// require()). The body is wrapped in an IIFE so the module-private
// declarations (VALID_CHOICES, ConsentManager, _exports) stay function-scoped.
// Classic <script src> tags share ONE global lexical environment, so a bare
// top-level `const _exports` here would collide with the identical declaration
// in any other dual-mode lib loaded into the same page (e.g. modal-manager.js)
// -- a page-killing redeclaration SyntaxError that silently kills the whole
// renderer. require() scopes each module separately, which is why the Node
// test suite never catches this. Preventive hardening following #263/#264.
(function () {
const VALID_CHOICES = new Set(['once', 'session', 'persistent', 'deny']);

class ConsentManager {
  constructor({ send, openPrompt, closePrompt } = {}) {
    this._send         = send         || (() => {});
    this._openPrompt   = openPrompt   || (() => {});
    this._closePrompt  = closePrompt  || (() => {});
    // request_id → payload (so a re-render or focus-loss can recover state)
    this._pending = new Map();
  }

  get pendingCount() { return this._pending.size; }

  // Inbound: Cerebral asks for consent. Validate the payload's shape so
  // a malformed event doesn't blow up the renderer.
  handleConsentRequest(payload) {
    if (!payload || typeof payload !== 'object') return;
    const {
      request_id,
      tool_name,
      capability,
      capability_label,
      capability_description,
      args_preview,
      flags,
    } = payload;
    if (!request_id || typeof request_id !== 'string') return;
    if (!capability || typeof capability !== 'string')   return;

    const record = {
      request_id,
      tool_name:              tool_name              || '',
      capability,
      capability_label:       capability_label       || capability,
      capability_description: capability_description || '',
      args_preview:           args_preview           || {},
      flags:                  flags                  || { passive: false, irreversible: false },
    };
    this._pending.set(request_id, record);
    this._openPrompt(record);
  }

  // Inbound: the user clicked a button (or hit Escape, which the UI
  // layer can map to 'deny').
  respond(request_id, choice) {
    if (!this._pending.has(request_id)) {
      // Stale or unknown request — ignore. Cerebral already moved on
      // (timeout fired) and there's nothing to send.
      return false;
    }
    if (!VALID_CHOICES.has(choice)) {
      // Defensive: treat an unknown choice as a deny so we never
      // accidentally upgrade to a grant.
      choice = 'deny';
    }
    this._pending.delete(request_id);
    this._closePrompt(request_id);
    this._send({
      type: 'consent_response',
      data: { request_id, choice },
    });
    return true;
  }

  // Cerebral disconnected — drop everything and ask the UI layer to
  // close any open prompts so we don't strand a window after a reconnect.
  reset() {
    for (const id of this._pending.keys()) {
      this._closePrompt(id);
    }
    this._pending.clear();
  }

  // Read-only accessor for tests and the UI layer.
  get(request_id) {
    return this._pending.get(request_id);
  }
}

// ── Dual-mode export ────────────────────────────────────────────────────
// Node (tests, main.js): module.exports = { ... }
// Browser (renderer):    window.ConsentManagerMod = { ... }
const _exports = { ConsentManager, VALID_CHOICES };

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.ConsentManagerMod = _exports;
}
})();
