'use strict';

// Tray-side irreversible-flag modal surface (Issue #49, ADR-0005).
//
// Cerebral emits an `irreversible_modal_request` event when a tool call
// declares `irreversible=True` AND the gate/ACL did not already DENY.
// This manager:
//   1. records the open request keyed by request_id
//   2. asks the UI layer to display a two-button modal for that request
//   3. routes the user's Accept / Cancel back to Cerebral as
//      `irreversible_modal_response`
//
// Unknown choices — including the consent surface's Once/Session/
// Persistent/Deny verbs — coerce to `cancel` so a malformed message can
// never accidentally dispatch an irreversible action.
//
// The UI layer is injected as two callbacks (`openPrompt`, `closePrompt`)
// so this class stays UI-free and unit-testable. main.js wires the real
// Electron BrowserWindow lifecycle into those callbacks.
//
// Dual-mode export: the same source feeds the Node test suite (and main.js)
// via require() AND can be loaded into a renderer via a plain <script src>
// tag (the windows run nodeIntegration:false per ADR-0007 so they can't
// require()). The body is wrapped in an IIFE so the module-private
// declarations (VALID_MODAL_CHOICES, ModalManager, _exports) stay
// function-scoped. Classic <script src> tags share ONE global lexical
// environment, so a bare top-level `const _exports` here would collide with
// the identical declaration in any other dual-mode lib loaded into the same
// page (e.g. consent-manager.js) -- a page-killing redeclaration SyntaxError
// that silently kills the whole renderer. require() scopes each module
// separately, which is why the Node test suite never catches this. Preventive
// hardening following #263/#264.
(function () {
const VALID_MODAL_CHOICES = new Set(['accept', 'cancel']);

class ModalManager {
  constructor({ send, openPrompt, closePrompt } = {}) {
    this._send         = send         || (() => {});
    this._openPrompt   = openPrompt   || (() => {});
    this._closePrompt  = closePrompt  || (() => {});
    // request_id → payload (so a re-render or focus-loss can recover state)
    this._pending = new Map();
  }

  get pendingCount() { return this._pending.size; }

  // Inbound: Cerebral asks for confirmation. Validate the payload's
  // shape so a malformed event doesn't blow up the renderer.
  handleModalRequest(payload) {
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
      // `irreversible: true` is the *defining* property of this prompt.
      // Default to it when the field is missing so the badge still renders
      // on a sparse payload — same defensive default as consent-manager.js.
      flags:                  flags                  || { passive: false, irreversible: true },
    };
    this._pending.set(request_id, record);
    this._openPrompt(record);
  }

  // Inbound: the user clicked Accept / Cancel (or hit Escape, which the
  // UI layer maps to Cancel).
  respond(request_id, choice) {
    if (!this._pending.has(request_id)) {
      // Stale or unknown request — ignore. Cerebral already moved on
      // (timeout fired) and there's nothing to send.
      return false;
    }
    if (!VALID_MODAL_CHOICES.has(choice)) {
      // Defensive: treat an unknown choice (or a consent-surface verb
      // sent here by mistake) as cancel so we never accidentally
      // dispatch the irreversible action.
      choice = 'cancel';
    }
    this._pending.delete(request_id);
    this._closePrompt(request_id);
    this._send({
      type: 'irreversible_modal_response',
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
// Browser (renderer):    window.ModalManagerMod = { ... }
const _exports = { ModalManager, VALID_MODAL_CHOICES };

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.ModalManagerMod = _exports;
}
})();
