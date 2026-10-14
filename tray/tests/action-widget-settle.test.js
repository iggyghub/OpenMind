'use strict';

// ActionWidget.settle(): a clicked action button must not stay on "Working..."
// when its tool returns without a panel_spec re-broadcast (cancelled picker,
// error). Fake DOM only.

const ActionWidget = require('../lib/action-widget');

function makeWidget(tool) {
  const btn = { disabled: false, handlers: {}, addEventListener(t, fn) { this.handlers[t] = fn; } };
  const status = { textContent: '' };
  const attrs = { 'data-tool': tool, 'data-tool-args': '{}' };
  const el = {
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    querySelector: (sel) => {
      if (sel === '.ps-action-btn') return btn;
      if (sel === '.ps-action-status') return status;
      return null;
    },
  };
  return { el, btn, status };
}

function container(els) {
  return {
    querySelectorAll: (sel) => (sel === '.ps-action' ? els : []),
  };
}

test('click shows Working..., settle clears it and re-enables the button', () => {
  const w = makeWidget('rea_browse_analyze');
  const sent = [];
  ActionWidget.initActionWidgets(container([w.el]), (m) => sent.push(m));
  w.btn.handlers.click();
  expect(w.btn.disabled).toBe(true);
  expect(w.status.textContent).not.toBe('');
  expect(sent[0].data.name).toBe('rea_browse_analyze');

  expect(ActionWidget.settle('rea_browse_analyze')).toBe(true);
  expect(w.btn.disabled).toBe(false);
  expect(w.status.textContent).toBe('');
});

test('settle returns false for a tool nothing is waiting on, and only once', () => {
  const w = makeWidget('some_tool');
  ActionWidget.initActionWidgets(container([w.el]), () => {});
  expect(ActionWidget.settle('unrelated_tool')).toBe(false);
  w.btn.handlers.click();
  expect(ActionWidget.settle('some_tool')).toBe(true);
  expect(ActionWidget.settle('some_tool')).toBe(false);
});
