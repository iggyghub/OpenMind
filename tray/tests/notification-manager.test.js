'use strict';

const { NotificationManager } = require('../lib/notification-manager');

let notify;
let onPersist;
let manager;

beforeEach(() => {
  jest.useFakeTimers();
  notify    = jest.fn();
  onPersist = jest.fn();
});

afterEach(() => {
  if (manager) { manager.destroy(); manager = null; }
  jest.useRealTimers();
});

// ── Cycle 5: tracer bullet — default state ────────────────────────────────────

test('notifications disabled by default', () => {
  manager = new NotificationManager({ notify });
  expect(manager.enabled).toBe(false);
});

// ── Cycle 6: default interval ─────────────────────────────────────────────────

test('reminder interval defaults to 120 minutes', () => {
  manager = new NotificationManager({ notify });
  expect(manager.intervalMinutes).toBe(120);
});

// ── Cycle 7: no notification when disabled ────────────────────────────────────

test('handleQueueUpdate does not fire when notifications disabled', () => {
  manager = new NotificationManager({ notify });
  manager.handleQueueUpdate([{ id: '1', title: 'Do something' }]);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 8: fires when enabled and queue grows ───────────────────────────────

test('fires notification when enabled and a new item is added', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Do something' }]);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('notification title contains "queue" (case-insensitive)', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Do something' }]);
  expect(notify.mock.calls[0][0]).toMatch(/queue/i);
});

// ── Cycle 9: no duplicate fire on stable queue ────────────────────────────────

test('does not fire when same items arrive again (count unchanged)', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]); // no change
  expect(notify).not.toHaveBeenCalled();
});

test('does not fire when queue shrinks (item dismissed)', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'A' }, { id: '2', title: 'B' }]);
  notify.mockClear();
  manager.handleQueueUpdate([{ id: '1', title: 'A' }]); // dismissed one
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 10: one notification per growth event, not per item ─────────────────

test('fires exactly once when multiple items added in one update', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'A' }, { id: '2', title: 'B' }]);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('fires again when queue grows further after first notification', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'A' }]);
  notify.mockClear();
  manager.handleQueueUpdate([{ id: '1', title: 'A' }, { id: '2', title: 'B' }]);
  expect(notify).toHaveBeenCalledTimes(1);
});

// ── Cycle 11: periodic reminder ───────────────────────────────────────────────

test('periodic reminder fires after interval when queue is non-empty', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();

  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('periodic reminder does not fire when queue is empty', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([]); // empty queue
  notify.mockClear();

  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 12: interval = 0 disables reminder ─────────────────────────────────

test('reminder does not run when interval is 0', () => {
  manager = new NotificationManager({ notify });
  manager.setEnabled(true);
  manager.setIntervalMinutes(0);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();

  jest.advanceTimersByTime(999 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 12b: disabled notifications suppress reminder ───────────────────────

test('periodic reminder does not fire when notifications disabled', () => {
  manager = new NotificationManager({ notify });
  // don't call setEnabled — stays false
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);

  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 13: initial state from constructor options ─────────────────────────

test('loads enabled=true from initialEnabled', () => {
  manager = new NotificationManager({ initialEnabled: true, notify });
  expect(manager.enabled).toBe(true);
});

test('loads custom interval from initialInterval', () => {
  manager = new NotificationManager({ initialInterval: 60, notify });
  expect(manager.intervalMinutes).toBe(60);
});

// ── Cycle 14: mutations call onPersist ────────────────────────────────────────

test('setEnabled calls onPersist with notifications_enabled', () => {
  manager = new NotificationManager({ onPersist, notify });
  manager.setEnabled(true);
  expect(onPersist).toHaveBeenCalledWith('notifications_enabled', true);
});

test('setIntervalMinutes calls onPersist with reminder_interval_minutes', () => {
  manager = new NotificationManager({ onPersist, notify });
  manager.setIntervalMinutes(30);
  expect(onPersist).toHaveBeenCalledWith('reminder_interval_minutes', 30);
});

// ── Cycle 15: click callback ──────────────────────────────────────────────────

test('onNotificationClick is invoked when notification fires', () => {
  const onClick = jest.fn();
  manager = new NotificationManager({
    notify,
    onNotificationClick: onClick,
  });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);

  // notify receives the click callback as 3rd arg; simulate the user clicking
  const clickCb = notify.mock.calls[0][2];
  clickCb();
  expect(onClick).toHaveBeenCalled();
});

// ── Cycle 16: applySettings syncs state from Cerebral broadcast ──────────────

test('applySettings updates enabled and restarts reminder', () => {
  manager = new NotificationManager({ notify });
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);

  manager.applySettings({ notifications_enabled: true, reminder_interval_minutes: 60 });
  expect(manager.enabled).toBe(true);
  expect(manager.intervalMinutes).toBe(60);

  notify.mockClear();
  jest.advanceTimersByTime(60 * 60 * 1000);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('applySettings disabling stops the reminder', () => {
  manager = new NotificationManager({ initialEnabled: true, notify });
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();

  manager.applySettings({ notifications_enabled: false });
  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

test('applySettings ignores unknown keys', () => {
  manager = new NotificationManager({ notify });
  // Should not throw
  manager.applySettings({ bogus_key: true });
  expect(manager.enabled).toBe(false);
});
