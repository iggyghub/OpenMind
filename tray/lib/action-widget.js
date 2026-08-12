'use strict';

// Action-widget event handler (S5 #542, ADR-0012 decision 3 / ADR-0014
// decision 8 -- the Skills panel's install field, enable/disable toggle,
// and uninstall button).
//
// Attaches click logic to every .ps-action element rendered by panel-spec.js.
// On click: sends a call_tool WS message to the tool declared in data-tool /
// data-tool-args, plus the input's value under data-input-arg when present.
// The status indicator is cleared when the panel re-renders on the incoming
// panel_spec broadcast (the whole panel is replaced, resetting the DOM) --
// same lifecycle as text-widget.js's Save button.
//
// Dual-mode: same source feeds the renderer via <script src> (exports on
// window.ActionWidget) and the Node tests via require(). IIFE-wrapped so
// private helpers stay function-scoped (see renderer-script-globals.test.js).

(function () {
  // Pure: build the call_tool WS message for an action click.
  // Exported so tests can verify the payload without touching the DOM.
  function buildActionMessage(
    tool, toolArgs, inputArg, inputValue, inputArg2, inputValue2, checkboxArg, checkboxValue
  ) {
    var args = Object.assign({}, toolArgs || {});
    if (inputArg) args[inputArg] = inputValue;
    // Optional second field (e.g. category alongside a URL). Omitted when the
    // action has no input_arg2 -- backward-compatible with single-input widgets.
    if (inputArg2) args[inputArg2] = inputValue2;
    // Optional boolean field (e.g. a verify checkbox) -- sent as a real boolean.
    if (checkboxArg) args[checkboxArg] = !!checkboxValue;
    return { type: 'call_tool', data: { name: tool || '', args: args } };
  }

  // Attach click handlers to every .ps-action in container.
  // sendFn is called with the WS message object (caller does JSON.stringify + ws.send).
  function initActionWidgets(container, sendFn) {
    if (!container || typeof container.querySelectorAll !== 'function') return;
    var widgets = container.querySelectorAll('.ps-action');
    for (var i = 0; i < widgets.length; i++) {
      (function (el) {
        var btn    = el.querySelector('.ps-action-btn');
        var input  = el.querySelector('.ps-action-input');
        var input2 = el.querySelector('.ps-action-input2');
        var check  = el.querySelector('.ps-action-check');
        var status = el.querySelector('.ps-action-status');
        if (!btn) return;
        var tool = el.getAttribute('data-tool') || '';
        var toolArgs = {};
        try { toolArgs = JSON.parse(el.getAttribute('data-tool-args') || '{}'); } catch (_) {}
        var inputArg  = el.getAttribute('data-input-arg') || '';
        var inputArg2 = el.getAttribute('data-input-arg2') || '';
        var checkArg  = el.getAttribute('data-checkbox-arg') || '';
        btn.addEventListener('click', function () {
          if (status) { status.textContent = 'Working…'; }
          btn.disabled = true;
          sendFn(buildActionMessage(
            tool, toolArgs,
            inputArg, input ? input.value : '',
            inputArg2, input2 ? input2.value : '',
            checkArg, check ? check.checked : false
          ));
        });
      })(widgets[i]);
    }
    initToggleWidgets(container, sendFn);
  }

  // Attach change handlers to every .ps-toggle switch in container. Flipping
  // the switch fires enable_tool (now on) or disable_tool (now off). The switch
  // stays in its new position optimistically -- the panel_spec re-broadcast the
  // tool triggers replaces the DOM and reflects the real state.
  function initToggleWidgets(container, sendFn) {
    if (!container || typeof container.querySelectorAll !== 'function') return;
    var toggles = container.querySelectorAll('.ps-toggle');
    for (var i = 0; i < toggles.length; i++) {
      (function (el) {
        var input  = el.querySelector('.ps-toggle-input');
        var status = el.querySelector('.ps-toggle-status');
        if (!input) return;
        var toolArgs = {};
        try { toolArgs = JSON.parse(el.getAttribute('data-tool-args') || '{}'); } catch (_) {}
        var enableTool  = el.getAttribute('data-enable-tool') || '';
        var disableTool = el.getAttribute('data-disable-tool') || '';
        input.addEventListener('change', function () {
          var label = el.querySelector('.ps-toggle-label');
          var tool = input.checked ? enableTool : disableTool;
          if (label) {
            label.textContent = input.checked ? 'Enabled' : 'Disabled';
          }
          if (status) {
            status.textContent = '';
            status.classList.add('ps-toggle-status--loading');
          }
          input.disabled = true;
          sendFn(buildActionMessage(tool, toolArgs, '', ''));
        });
      })(toggles[i]);
    }
  }

  var _exports = {
    buildActionMessage: buildActionMessage,
    initActionWidgets:  initActionWidgets,
    initToggleWidgets:  initToggleWidgets,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.ActionWidget = _exports;
  }
})();
