'use strict';

// Felix as a mark-kit host (tools/mark-kit). Everything Felix-specific about
// mark mode lives here; the core stays host-agnostic.
//
//   context(el)   -> { route, subtab } for each mark
//   capture(rect) -> screenshot of Felix's own window. The Main window has no
//                    preload (ADR-0007), so the request goes over the WS bridge:
//                    renderer -> Cerebral (relay) -> tray main process, which
//                    runs webContents.capturePage(rect) and answers with
//                    mark_capture_result through the same relay.
//   onEvent(ev)   -> called from main.html's handleEvent for every Cerebral event.
//
// Dual-mode: window.MarkKitHost in the renderer, module.exports for jest.

(function () {
  var CAPTURE_TIMEOUT_MS = 2500;

  function create(send) {
    var waiting = {};  // capture id -> resolve
    var seq = 0;

    function context(el) {
      var pane = el.closest('[data-route]');
      var tabs = pane ? Array.prototype.filter.call(pane.querySelectorAll('.tab-strip .is-active'),
        function (t) { return t.offsetParent !== null; })  // hidden sub-panes keep their own active tab
        .map(function (t) { return t.textContent.trim(); }) : [];
      return { route: pane ? pane.getAttribute('data-route') : '', subtab: tabs.join(' > ') };
    }

    function capture(rect) {
      return new Promise(function (resolve) {
        var id = 'cap-' + Date.now() + '-' + (++seq);
        waiting[id] = resolve;
        if (!send({ type: 'mark_capture', data: { id: id, rect: rect } })) { delete waiting[id]; resolve(null); return; }
        setTimeout(function () { if (waiting[id]) { delete waiting[id]; resolve(null); } }, CAPTURE_TIMEOUT_MS);
      });
    }

    function onEvent(ev) {
      if (ev.type === 'mark_capture_result' && ev.data && waiting[ev.data.id]) {
        var resolve = waiting[ev.data.id];
        delete waiting[ev.data.id];
        resolve(ev.data.png || null);
      }
    }

    return { context: context, capture: capture, onEvent: onEvent };
  }

  var _exports = { create: create };
  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.MarkKitHost = _exports;
  }
})();
