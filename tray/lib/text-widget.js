'use strict';

// Text-widget event handler (UI2 A4 -- #484, ADR-0012 decisions 4 & 5).
//
// Attaches Save-button logic to every .ps-text element rendered by panel-spec.js.
// On save: reads textarea.value, sends a call_tool WS message to the plugin tool
// declared in data-tool / data-tool-args, then shows a "Saving..." indicator.
// The indicator is cleared when the panel re-renders on the incoming panel_spec
// broadcast (the whole panel is replaced, resetting the DOM).
//
// Dual-mode: same source feeds the renderer via <script src> (exports on
// window.TextWidget) and the Node tests via require(). IIFE-wrapped so private
// helpers stay function-scoped (see renderer-script-globals.test.js).

(function () {
  // Pure: build the call_tool WS message for a save action.
  // Exported so tests can verify the payload without touching the DOM.
  function buildSaveMessage(tool, toolArgs, content) {
    var args = Object.assign({}, toolArgs || {});
    args.content = content;
    return { type: 'call_tool', data: { name: tool || '', args: args } };
  }

  // Attach save-button + dirty-tracking handlers to every .ps-text in container.
  // sendFn is called with the WS message object (caller does JSON.stringify + ws.send).
  function initTextWidgets(container, sendFn) {
    if (!container || typeof container.querySelectorAll !== 'function') return;
    var widgets = container.querySelectorAll('.ps-text');
    for (var i = 0; i < widgets.length; i++) {
      (function (el) {
        var textarea = el.querySelector('.ps-text-area');
        var saveBtn  = el.querySelector('.ps-text-save');
        var status   = el.querySelector('.ps-text-status');
        if (!textarea || !saveBtn) return;
        var tool = el.getAttribute('data-tool') || '';
        var toolArgs = {};
        try { toolArgs = JSON.parse(el.getAttribute('data-tool-args') || '{}'); } catch (_) {}
        var original = textarea.value;
        textarea.addEventListener('input', function () {
          if (status) {
            status.textContent = textarea.value !== original ? 'Unsaved changes' : '';
          }
        });
        saveBtn.addEventListener('click', function () {
          if (status) { status.textContent = 'Saving…'; }
          saveBtn.disabled = true;
          sendFn(buildSaveMessage(tool, toolArgs, textarea.value));
        });
      })(widgets[i]);
    }
  }

  var _exports = {
    buildSaveMessage: buildSaveMessage,
    initTextWidgets:  initTextWidgets,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.TextWidget = _exports;
  }
})();
