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
//   store         -> the gated marks_add / marks_list / marks_update tools
//                    (plugins/marks.py) via call_tool with record:false (UI
//                    chrome, not a chat turn). tool_result carries no request
//                    id, so answers are taken first-in-first-out per tool name;
//                    the core matches stored marks back by their own id.
//   show(mark)    -> review: switch to the mark's route (sub-tab is left as is:
//                    mark mode swallows clicks, so a tab can't be clicked open).
//   shotUrl(mark) -> file:// URL of the stored screenshot (the window is file://).
//   onEvent(ev)   -> called from main.html's handleEvent for every Cerebral event.
//
// Dual-mode: window.MarkKitHost in the renderer, module.exports for jest.

(function () {
  var CAPTURE_TIMEOUT_MS = 2500;

  function create(send) {
    var waiting = {};  // capture id -> resolve
    var queues = {};   // tool name -> [resolve], oldest first
    var seq = 0;

    // -> parsed JSON result, or null (offline, refused by the gate, error).
    function call(name, args) {
      return new Promise(function (resolve) {
        (queues[name] = queues[name] || []).push(resolve);
        if (!send({ type: 'call_tool', data: { name: name, args: args, record: false } })) {
          queues[name].pop(); resolve(null);
        }
      });
    }
    var store = {
      add: function (mark) {
        var m = Object.assign({}, mark), shot = m.shot;
        delete m.shot; delete m.stored;
        return call('marks_add', shot ? { mark: m, shot: shot } : { mark: m });
      },
      list: function () {
        return call('marks_list', {}).then(function (r) { return r && r.marks; });
      },
      update: function (id, patch) {
        return call('marks_update', Object.assign({ id: id }, patch));
      },
    };

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
      var q = ev.type === 'tool_result' && ev.data && queues[ev.data.name];
      if (q && q.length) {
        var r = null;
        if (!ev.data.is_error) { try { r = JSON.parse(ev.data.content); } catch (e) {} }
        q.shift()(r);
        return;
      }
      if (ev.type === 'mark_capture_result' && ev.data && waiting[ev.data.id]) {
        var resolve = waiting[ev.data.id];
        delete waiting[ev.data.id];
        resolve(ev.data.png || null);
      }
    }

    function show(m) {
      var route = m.app && m.app.route;
      if (route && typeof location !== 'undefined' && location.hash !== '#' + route) location.hash = '#' + route;
    }
    function shotUrl(m) {
      return m.shot_abs ? 'file:///' + encodeURI(m.shot_abs.replace(/\\/g, '/')) : null;
    }

    return { context: context, capture: capture, store: store, show: show, shotUrl: shotUrl, onEvent: onEvent };
  }

  var _exports = { create: create };
  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.MarkKitHost = _exports;
  }
})();
