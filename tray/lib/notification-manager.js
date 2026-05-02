'use strict';

const DEFAULTS = {
  notifications_enabled:    false,
  reminder_interval_minutes: 120,
};

class NotificationManager {
  constructor({ store, notify, onNotificationClick } = {}) {
    this._store             = store;
    this._notify            = notify            || (() => {});
    this._onNotificationClick = onNotificationClick || (() => {});

    this._enabled         = store.get('notifications_enabled')      ?? DEFAULTS.notifications_enabled;
    this._intervalMinutes = store.get('reminder_interval_minutes')   ?? DEFAULTS.reminder_interval_minutes;
    this._pendingCount    = 0;
    this._reminderTimer   = null;

    // If persisted settings say notifications are on, start the reminder now.
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
    this._store.set('notifications_enabled', value);

    this._stopReminder();
    if (value && this._intervalMinutes > 0) {
      this._startReminder();
    }
  }

  setIntervalMinutes(minutes) {
    this._intervalMinutes = minutes;
    this._store.set('reminder_interval_minutes', minutes);

    this._stopReminder();
    if (this._enabled && minutes > 0) {
      this._startReminder();
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

module.exports = { NotificationManager };
