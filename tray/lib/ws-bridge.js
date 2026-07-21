'use strict';

// WebSocket bridge to Cerebral (UI2 A5 -- #485, extracted for detached panels).
//
// Owns the socket lifecycle: connect, JSON envelope, reconnect on close. No
// routing -- callers hand it onOpen/onMessage/onClose. The Main window and each
// detached panel window construct their own bridge, so the extraction is
// mandatory before A5 can add a second BrowserWindow (ADR-0012 decision 2).
//
// Dual-mode: same source feeds the renderer via <script src> (window.WsBridge)
// and the Node tests via require(). IIFE-wrapped so private consts stay
// function-scoped (see renderer-script-globals.test.js).

(function () {
  var DEFAULT_RECONNECT_DELAY_MS = 3000;
  var WS_OPEN = 1;

  function create(opts) {
    opts = opts || {};
    var url         = opts.url;
    var onOpen      = opts.onOpen    || function () {};
    var onMessage   = opts.onMessage || function () {};
    var onClose     = opts.onClose   || function () {};
    var reconnectMs = (typeof opts.reconnectDelayMs === 'number')
      ? opts.reconnectDelayMs : DEFAULT_RECONNECT_DELAY_MS;
    var wsFactory   = opts.wsFactory || function (u) { return new WebSocket(u); };
    var setTimer    = opts.setTimeoutFn || (typeof setTimeout === 'function' ? setTimeout : null);

    var ws     = null;
    var closed = false;

    function _connect() {
      if (closed) return;
      ws = wsFactory(url);
      ws.addEventListener('open',  function ()  { onOpen(); });
      ws.addEventListener('close', function ()  {
        onClose();
        if (!closed && setTimer) setTimer(_connect, reconnectMs);
      });
      // error is followed by close; nothing to do here.
      ws.addEventListener('error', function () {});
      ws.addEventListener('message', function (e) {
        var event;
        try { event = JSON.parse(e.data); } catch (_) { return; }
        onMessage(event);
      });
    }

    function send(event) {
      if (!ws || ws.readyState !== WS_OPEN) return false;
      ws.send(JSON.stringify(event));
      return true;
    }

    function isOpen() {
      return !!ws && ws.readyState === WS_OPEN;
    }

    // Stops reconnects and closes the underlying socket. After close() the
    // bridge is dead -- create a new one to reconnect. Used by a detached
    // panel window on unload so it does not leave a reconnect timer behind.
    function close() {
      closed = true;
      if (ws) { try { ws.close(); } catch (_) {} }
    }

    _connect();
    return { send: send, isOpen: isOpen, close: close };
  }

  var _exports = {
    create: create,
    DEFAULT_RECONNECT_DELAY_MS: DEFAULT_RECONNECT_DELAY_MS,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.WsBridge = _exports;
  }
})();
