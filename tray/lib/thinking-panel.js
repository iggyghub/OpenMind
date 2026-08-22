/* Thinking panel helpers -- #816/#824/#827.
 * Dual-mode: window.ThinkingPanelMod in the renderer; module.exports for
 * Node tests. Pure text/HTML-string shapers -- no DOM, no WS -- matching
 * the documents-panel.js convention so this stays testable without jsdom
 * (not an installed dependency in this project). The renderer owns
 * inserting rowHtml() output and pruning the feed to FEED_MAX.
 *
 * #827 -- reads the real conversation_turn_emitted wire shape:
 * { kind, content, ts }, where content is the raw dict chain_engine.py /
 * main.py record (e.g. content.name, content.args, content.result,
 * content.text) -- see cerebral/db/conversation.py's to_dict() and
 * labelFor() in main.html for the same field access pattern. The
 * pre-#827 version read turn.tool_call.name / turn.tool_result.result,
 * which never matched any real turn and rendered blank rows -- caught
 * before it shipped further, not from a live bug report.
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
  var GROUP_HEADER_MAX_CHARS = 80;
  var RESULT_MAX_CHARS = 150;

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // "gmail_search" -> "Gmail Search". No per-tool phrasing -- the registry
  // has 200+ tools, a bespoke verb/phrase per tool isn't maintainable.
  function humanizeToolName(name) {
    return String(name || 'tool')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /* HTML for a group header (the user message that kicked off the steps
   * under it), or '' if this turn isn't a user turn. */
  function groupHeaderHtml(turn) {
    if (!turn || (turn.kind !== 'user_text' && turn.kind !== 'user_voice')) return '';
    var text = String((turn.content && turn.content.text) || '').trim();
    var trimmed = text.length > GROUP_HEADER_MAX_CHARS
      ? text.slice(0, GROUP_HEADER_MAX_CHARS) + '…' : text;
    return '<div class="thinking-group-header">' + escHtml(trimmed || '(voice message)') + '</div>';
  }

  /* HTML for one tool step row, or '' if this turn isn't tool_call/
   * tool_result. Escapes tool output text -- it can contain arbitrary
   * content from a web page, file, or user data a tool touched. */
  function stepRowHtml(turn) {
    if (!turn) return '';
    var content = turn.content || {};
    if (turn.kind === 'tool_call') {
      return '<div class="thinking-row tool_call">' + escHtml(humanizeToolName(content.name)) + '</div>';
    }
    if (turn.kind === 'tool_result') {
      var text = String(content.result || '').replace(/\n/g, ' ').slice(0, RESULT_MAX_CHARS);
      var cls = 'thinking-row tool_result' + (content.is_error ? ' is-error' : '');
      var prefix = content.is_error ? 'Error: ' : '';
      return '<div class="' + cls + '">' + escHtml(prefix + text) + '</div>';
    }
    return '';
  }

  /* HTML for one feed entry -- a group header for a user turn, a step row
   * for a tool_call/tool_result turn, or '' for anything else (caller
   * skips appending in that case). */
  function rowHtml(turn) {
    var group = groupHeaderHtml(turn);
    if (group) return group;
    return stepRowHtml(turn);
  }

  return {
    FEED_MAX:         FEED_MAX,
    escHtml:          escHtml,
    humanizeToolName: humanizeToolName,
    groupHeaderHtml:  groupHeaderHtml,
    stepRowHtml:      stepRowHtml,
    rowHtml:          rowHtml,
  };
}));
