'use strict';

// Panel spec renderer (UI2 A3 -- #483, ADR-0012 decision 3).
//
// Plugins contribute panels as declarative specs; this module owns all the
// drawing. A plugin never ships HTML or JavaScript into the renderer -- it
// returns JSON describing a widget tree from a fixed vocabulary, and the
// renderer maps that onto safe markup.
//
// v1 vocabulary (this slice): { list, detail }. The text widget is A4 (#484).
//
// SECURITY: every string that came from a plugin is passed through escHtml
// before it reaches the returned markup. An HTML-bearing plugin value is
// escaped, never executed. Unknown widget types are ignored (return ''), so
// a malformed spec cannot inject markup.
//
// Dual-mode: same source feeds the renderer via <script src> (exports on
// window.PanelSpec) and the Node tests via require(). IIFE-wrapped so the
// private helpers stay function-scoped (see renderer-script-globals.test.js).

(function () {
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _renderList(w) {
    var items = Array.isArray(w && w.items) ? w.items : [];
    if (items.length === 0) {
      return '<div class="ps-empty">Empty.</div>';
    }
    var rows = items.map(function (it) {
      if (!it || typeof it !== 'object') return '';
      var title = escHtml(it.title || '');
      var sub   = it.subtitle
        ? '<div class="ps-list-sub">' + escHtml(it.subtitle) + '</div>'
        : '';
      return '<li class="ps-list-item">' +
        '<div class="ps-list-title">' + title + '</div>' +
        sub +
      '</li>';
    }).join('');
    return '<ul class="ps-list">' + rows + '</ul>';
  }

  function _renderDetail(w) {
    var fields = Array.isArray(w && w.fields) ? w.fields : [];
    if (fields.length === 0) {
      return '<div class="ps-empty">No details.</div>';
    }
    var rows = fields.map(function (f) {
      if (!f || typeof f !== 'object') return '';
      return '<div class="ps-detail-row">' +
        '<div class="ps-detail-label">' + escHtml(f.label || '') + '</div>' +
        '<div class="ps-detail-value">' + escHtml(f.value == null ? '' : f.value) + '</div>' +
      '</div>';
    }).join('');
    return '<div class="ps-detail">' + rows + '</div>';
  }

  // UI2 A4 (#484): text widget -- editable <textarea> that saves back via a plugin tool.
  // value is HTML-escaped so a file containing <tags> is inert when inserted via innerHTML.
  // The save handler (text-widget.js) reads textarea.value which un-escapes automatically.
  function _renderText(w) {
    var id       = escHtml(w.id || '');
    var tool     = escHtml(w.tool || '');
    var toolArgs = escHtml(JSON.stringify(w.tool_args != null ? w.tool_args : {}));
    var value    = escHtml(String(w.value == null ? '' : w.value));
    var label    = w.label
      ? '<div class="ps-text-label">' + escHtml(w.label) + '</div>'
      : '';
    return (
      '<div class="ps-text"' +
        ' data-widget-id="' + id + '"' +
        ' data-tool="'      + tool + '"' +
        ' data-tool-args="' + toolArgs + '">' +
        label +
        '<textarea class="ps-text-area">' + value + '</textarea>' +
        '<div class="ps-text-toolbar">' +
          '<span class="ps-text-status"></span>' +
          '<button class="ps-text-save">Save</button>' +
        '</div>' +
      '</div>'
    );
  }

  // S5 #542 (Skills panel, ADR-0014 decision 8): action widget -- a button
  // that calls a plugin tool with fixed tool_args, optionally plus one
  // user-typed value (input_arg names which arg the input's value fills).
  // No input_arg -> a plain confirm-style button (uninstall, enable/disable).
  // Handler lives in action-widget.js, same shape as the text widget's Save.
  function _renderAction(w) {
    var id       = escHtml(w.id || '');
    var tool     = escHtml(w.tool || '');
    var toolArgs = escHtml(JSON.stringify(w.tool_args != null ? w.tool_args : {}));
    var label    = escHtml(w.label || 'Run');
    var inputArg = w.input_arg ? escHtml(w.input_arg) : '';
    var input    = inputArg
      ? '<input class="ps-action-input" type="text" placeholder="' +
          escHtml(w.input_placeholder || '') + '">'
      : '';
    return (
      '<div class="ps-action"' +
        ' data-widget-id="' + id + '"' +
        ' data-tool="'      + tool + '"' +
        ' data-tool-args="' + toolArgs + '"' +
        (inputArg ? ' data-input-arg="' + inputArg + '"' : '') +
        '>' +
        input +
        '<button class="ps-action-btn" type="button">' + label + '</button>' +
        '<span class="ps-action-status"></span>' +
      '</div>'
    );
  }

  var WIDGETS = {
    list:   _renderList,
    detail: _renderDetail,
    text:   _renderText,
    action: _renderAction,
  };

  // Renders one widget. Unknown or malformed types return '' -- inert.
  function renderWidget(w) {
    if (!w || typeof w !== 'object') return '';
    var fn = WIDGETS[w.type];
    if (typeof fn !== 'function') return '';
    return fn(w);
  }

  // Renders a full panel spec {title, widgets: [...]} into an HTML string.
  // Bad spec (null, non-object) yields an inert empty-state marker.
  function renderPanel(spec) {
    if (!spec || typeof spec !== 'object') {
      return '<div class="ps-empty">No panel data.</div>';
    }
    var title = spec.title
      ? '<h2 class="ps-title">' + escHtml(spec.title) + '</h2>'
      : '';
    var widgets = Array.isArray(spec.widgets) ? spec.widgets : [];
    var body = widgets.map(renderWidget).join('');
    return '<div class="ps-panel">' + title + body + '</div>';
  }

  var _exports = {
    escHtml:      escHtml,
    renderWidget: renderWidget,
    renderPanel:  renderPanel,
    WIDGET_TYPES: Object.keys(WIDGETS),
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.PanelSpec = _exports;
  }
})();
