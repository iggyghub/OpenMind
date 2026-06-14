'use strict';

// Dual-mode export: the same source feeds the Node test suite (and main.js)
// via require() AND can be loaded into a renderer via a plain <script src>
// tag (the windows run nodeIntegration:false per ADR-0007 so they can't
// require()). The body is wrapped in an IIFE so the module-private
// declarations (DEFAULTS, NotificationManager, _exports) stay function-scoped.
// Classic <script src> tags share ONE global lexical environment, so a bare
// top-level `const _exports` here would collide with the identical declaration
// in any other dual-mode lib loaded into the same page -- a page-killing
// redeclaration SyntaxError that silently kills the whole renderer. require()
// scopes each module separately, which is why the Node test suite never
// catches this. Preventive hardening following #263/#264.
(function () {
const DEFAULTS = {
  notifications_enabled:    false,
  reminder_interval_minutes: 120,
};

class NotificationManager {
  constructor({ initialEnabled, initialInterval, onPersist, notify, onNotificationClick } = {}) {
    this._enabled         = initialEnabled         ?? DEFAULTS.notifications_enabled;
    this._intervalMinutes = initialInterval         ?? DEFAULTS.reminder_interval_minutes;
    this._onPersist       = onPersist              || (() => {});
    this._notify          = notify                 || (() => {});
    this._onNotificationClick = onNotificationClick || (() => {});

    this._pendingCount  = 0;
    this._reminderTimer = null;

    if (this._enabled && this._intervalMinutes > 0) {
      this._startReminder();
    }
  }

  // ── Public getters ──────────────────────────────────────────────────────────

  get enabled()         { return this._enabled; }
  get intervalMinutes() { return this._intervalMinutes; }

  // ── Public setters ──────────────────────────────────────────────────────────

  setEnabled(value) {
    this._enabled = value;
    this._onPersist('notifications_enabled', value);

    this._stopReminder();
    if (value && this._intervalMinutes > 0) {
      this._startReminder();
    }
  }

  setIntervalMinutes(minutes) {
    this._intervalMinutes = minutes;
    this._onPersist('reminder_interval_minutes', minutes);

    this._stopReminder();
    if (this._enabled && minutes > 0) {
      this._startReminder();
    }
  }

  // ── Settings sync from Cerebral broadcast ───────────────────────────────────

  applySettings(settings) {
    const prevEnabled  = this._enabled;
    const prevInterval = this._intervalMinutes;

    if ('notifications_enabled' in settings) {
      this._enabled = settings.notifications_enabled;
    }
    if ('reminder_interval_minutes' in settings) {
      this._intervalMinutes = settings.reminder_interval_minutes;
    }

    if (prevEnabled !== this._enabled || prevInterval !== this._intervalMinutes) {
      this._stopReminder();
      if (this._enabled && this._intervalMinutes > 0) {
        this._startReminder();
      }
    }
  }

  // ── Queue event ─────────────────────────────────────────────────────────────

  handleQueueUpdate(items) {
    const newCount = items.length;
    const grew     = newCount > this._pendingCount;
    this._pendingCount = newCount;

    if (this._enabled && grew) {
      this._fire(
        'Felix — Queue updated',
        `${newCount} pending action${newCount === 1 ? '' : 's'}`,
      );
    }
  }

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  destroy() {
    this._stopReminder();
  }

  // ── Private ─────────────────────────────────────────────────────────────────

  _fire(title, body) {
    this._notify(title, body, this._onNotificationClick);
  }

  _startReminder() {
    const ms = this._intervalMinutes * 60 * 1000;
    this._reminderTimer = setInterval(() => {
      if (this._enabled && this._pendingCount > 0) {
        this._fire(
          'Felix — Pending actions',
          `${this._pendingCount} action${this._pendingCount === 1 ? '' : 's'} still waiting`,
        );
      }
    }, ms);
  }

  _stopReminder() {
    if (this._reminderTimer !== null) {
      clearInterval(this._reminderTimer);
      this._reminderTimer = null;
    }
  }
}

// ── Dual-mode export ────────────────────────────────────────────────────
// Node (tests, main.js): module.exports = { ... }
// Browser (renderer):    window.NotificationManagerMod = { ... }
const _exports = { NotificationManager };

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.NotificationManagerMod = _exports;
}
})();
