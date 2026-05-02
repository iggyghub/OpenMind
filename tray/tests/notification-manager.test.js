'use strict';

const { NotificationManager } = require('../lib/notification-manager');

function makeStore(initial = {}) {
  const data = { ...initial };
  return {
    get: (key) => data[key],
    set: (key, val) => { data[key] = val; },
  };
}

let notify;
let manager;

beforeEach(() => {
  jest.useFakeTimers();
  notify = jest.fn();
});

afterEach(() => {
  if (manager) { manager.destroy(); manager = null; }
  jest.useRealTimers();
});

// ── Cycle 5: tracer bullet — default state ────────────────────────────────────

test('notifications disabled by default', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  expect(manager.enabled).toBe(false);
});

// ── Cycle 6: default interval ─────────────────────────────────────────────────

test('reminder interval defaults to 120 minutes', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  expect(manager.intervalMinutes).toBe(120);
});

// ── Cycle 7: no notification when disabled ────────────────────────────────────

test('handleQueueUpdate does not fire when notifications disabled', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.handleQueueUpdate([{ id: '1', title: 'Do something' }]);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 8: fires when enabled and queue grows ───────────────────────────────

test('fires notification when enabled and a new item is added', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Do something' }]);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('notification title contains "queue" (case-insensitive)', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Do something' }]);
  expect(notify.mock.calls[0][0]).toMatch(/queue/i);
});

// ── Cycle 9: no duplicate fire on stable queue ────────────────────────────────

test('does not fire when same items arrive again (count unchanged)', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]); // no change
  expect(notify).not.toHaveBeenCalled();
});

test('does not fire when queue shrinks (item dismissed)', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'A' }, { id: '2', title: 'B' }]);
  notify.mockClear();
  manager.handleQueueUpdate([{ id: '1', title: 'A' }]); // dismissed one
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 10: one notification per growth event, not per item ─────────────────

test('fires exactly once when multiple items added in one update', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'A' }, { id: '2', title: 'B' }]);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('fires again when queue grows further after first notification', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'A' }]);
  notify.mockClear();
  manager.handleQueueUpdate([{ id: '1', title: 'A' }, { id: '2', title: 'B' }]);
  expect(notify).toHaveBeenCalledTimes(1);
});

// ── Cycle 11: periodic reminder ───────────────────────────────────────────────

test('periodic reminder fires after interval when queue is non-empty', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();

  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('periodic reminder does not fire when queue is empty', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.handleQueueUpdate([]); // empty queue
  notify.mockClear();

  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 12: interval = 0 disables reminder ─────────────────────────────────

test('reminder does not run when interval is 0', () => {
  manager = new NotificationManager({ store: makeStore(), notify });
  manager.setEnabled(true);
  manager.setIntervalMinutes(0);
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);
  notify.mockClear();

  jest.advanceTimersByTime(999 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 12b: disabled notifications suppress reminder ───────────────────────

test('periodic reminder does not fire when notifications disabled', () => {
  // enabled=false means no reminder, even if queue has items
  manager = new NotificationManager({ store: makeStore(), notify });
  // don't call setEnabled — stays false
  manager.handleQueueUpdate([{ id: '1', title: 'Task' }]);

  jest.advanceTimersByTime(120 * 60 * 1000);
  expect(notify).not.toHaveBeenCalled();
});

// ── Cycle 13: settings load from store on construction ───────────────────────

test('loads enabled=true from store on construction', () => {
  manager = new NotificationManager({
    store: makeStore({ notifications_enabled: true }),
    notify,
  });
  expect(manager.enabled).toBe(true);
});

test('loads custom interval from store on construction', () => {
  manager = new NotificationManager({
    store: makeStore({ reminder_interval_minutes: 60 }),
    notify,
  });
  expect(manager.intervalMinutes).toBe(60);
});

// ── Cycle 14: settings persist to store ──────────────────────────────────────

test('setEnabled persists to store', () => {
  const store = makeStore();
  manager = new NotificationManager({ store, notify });
  manager.setEnabled(true);
  expect(store.get('notifications_enabled')).toBe(true);
});

test('setIntervalMinutes persists to store', () => {
  const store = makeStore();
  manager = new NotificationManager({ store, notify });
  manager.setIntervalMinutes(30);
  expect(store.get('reminder_interval_minutes')).toBe(30);
});

// ── Cycle 15: click callback ──────────────────────────────────────────────────

test('onNotificationClick is invoked when notification fires', () => {
  const onClick = jest.fn();
  manager = new NotificationManager({
    store: makeStore(),
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
