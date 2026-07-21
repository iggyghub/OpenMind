'use strict';

// Tests for tray/lib/ws-bridge.js (UI2 A5 -- #485).
// Pure state layer: constructs a socket via an injected factory, dispatches
// callbacks, reconnects on close, stops on close(). No DOM.

const WsBridge = require('../lib/ws-bridge');

function makeMockWs() {
  const listeners = Object.create(null);
  const ws = {
    readyState: 0,
    addEventListener(type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    send:  jest.fn(),
    close: jest.fn(),
  };
  ws._fire = (type, evt) => (listeners[type] || []).forEach((fn) => fn(evt));
  return ws;
}

describe('WsBridge.create', () => {
  test('forwards socket open events to onOpen', () => {
    const ws = makeMockWs();
    const onOpen = jest.fn();
    WsBridge.create({ url: 'ws://x', onOpen, wsFactory: () => ws, setTimeoutFn: () => {} });
    ws._fire('open', {});
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('parses JSON messages and dispatches to onMessage', () => {
    const ws = makeMockWs();
    const onMessage = jest.fn();
    WsBridge.create({ url: 'ws://x', onMessage, wsFactory: () => ws, setTimeoutFn: () => {} });
    ws._fire('message', { data: JSON.stringify({ type: 'hello', data: { x: 1 } }) });
    expect(onMessage).toHaveBeenCalledWith({ type: 'hello', data: { x: 1 } });
  });

  test('drops unparseable messages without throwing', () => {
    const ws = makeMockWs();
    const onMessage = jest.fn();
    WsBridge.create({ url: 'ws://x', onMessage, wsFactory: () => ws, setTimeoutFn: () => {} });
    expect(() => ws._fire('message', { data: 'not json' })).not.toThrow();
    expect(onMessage).not.toHaveBeenCalled();
  });

  test('send serialises the event when the socket is OPEN, no-op otherwise', () => {
    const ws = makeMockWs();
    const bridge = WsBridge.create({ url: 'ws://x', wsFactory: () => ws, setTimeoutFn: () => {} });
    // CONNECTING -- send is a no-op.
    expect(bridge.send({ type: 'ping' })).toBe(false);
    expect(ws.send).not.toHaveBeenCalled();
    ws.readyState = 1;
    expect(bridge.send({ type: 'ping' })).toBe(true);
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'ping' }));
    // CLOSED -- send stops again.
    ws.readyState = 3;
    expect(bridge.send({ type: 'ping' })).toBe(false);
  });

  test('isOpen reflects socket readyState', () => {
    const ws = makeMockWs();
    const bridge = WsBridge.create({ url: 'ws://x', wsFactory: () => ws, setTimeoutFn: () => {} });
    expect(bridge.isOpen()).toBe(false);
    ws.readyState = 1;
    expect(bridge.isOpen()).toBe(true);
    ws.readyState = 3;
    expect(bridge.isOpen()).toBe(false);
  });

  test('calls onClose when the socket closes', () => {
    const ws = makeMockWs();
    const onClose = jest.fn();
    WsBridge.create({ url: 'ws://x', onClose, wsFactory: () => ws, setTimeoutFn: () => {} });
    ws._fire('close', {});
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('reconnects via a fresh factory call after close', () => {
    const sockets = [];
    const factory = () => { const s = makeMockWs(); sockets.push(s); return s; };
    const setTimer = jest.fn();
    WsBridge.create({
      url: 'ws://x',
      wsFactory: factory,
      reconnectDelayMs: 500,
      setTimeoutFn: setTimer,
    });
    expect(sockets.length).toBe(1);
    sockets[0]._fire('close', {});
    // A reconnect was scheduled at the configured delay.
    expect(setTimer).toHaveBeenCalledTimes(1);
    expect(setTimer.mock.calls[0][1]).toBe(500);
    // Running the scheduled callback opens a fresh socket via the factory.
    setTimer.mock.calls[0][0]();
    expect(sockets.length).toBe(2);
  });

  test('close() prevents further reconnects and closes the socket', () => {
    const sockets = [];
    const factory = () => { const s = makeMockWs(); sockets.push(s); return s; };
    const setTimer = jest.fn();
    const bridge = WsBridge.create({
      url: 'ws://x',
      wsFactory: factory,
      setTimeoutFn: setTimer,
    });
    bridge.close();
    expect(sockets[0].close).toHaveBeenCalled();
    // A subsequent socket-close event must NOT schedule a reconnect.
    sockets[0]._fire('close', {});
    expect(setTimer).not.toHaveBeenCalled();
  });

  test('exposes a default reconnect delay constant', () => {
    expect(typeof WsBridge.DEFAULT_RECONNECT_DELAY_MS).toBe('number');
    expect(WsBridge.DEFAULT_RECONNECT_DELAY_MS).toBeGreaterThan(0);
  });
});
