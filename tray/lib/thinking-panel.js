/* Thinking panel helpers -- #816/#824.
 * Dual-mode: window.ThinkingPanelMod in the renderer; module.exports for
 * Node tests. Pure text/HTML-string shapers -- no DOM, no WS -- matching
 * the documents-panel.js convention so this stays testable without jsdom
 * (not an installed dependency in this project). The renderer owns
 * inserting rowHtml() output and pruning the feed to FEED_MAX.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.ThinkingPanelMod = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var FEED_MAX = 300;

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Mirror labelFor() formatting from main.html for tool kinds.
  function formatTurn(turn) {
    if (!turn) return '';
    if (turn.kind === 'tool_call') {
      var name = (turn.tool_call || {}).name || '';
      return '-> ' + name;
    }
    if (turn.kind === 'tool_result') {
      var res = (turn.tool_result || {}).result || '';
      return '<- ' + String(res).replace(/\n/g, ' ').slice(0, 150);
    }
    return '';
  }

  /* HTML for one feed row, or '' when the turn kind isn't tool_call/
   * tool_result (caller skips appending in that case). Escapes tool
   * output text -- it can contain arbitrary content from a web page,
   * file, or user data a tool touched. */
  function rowHtml(turn) {
    var text = formatTurn(turn);
    if (!text) return '';
    var kind = (turn && turn.kind) || '';
    return '<div class="thinking-row ' + kind + '">' + escHtml(text) + '</div>';
  }

  return {
    FEED_MAX:   FEED_MAX,
    escHtml:    escHtml,
    formatTurn: formatTurn,
    rowHtml:    rowHtml,
  };
}));
