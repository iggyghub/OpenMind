'use strict';

(function () {
  var FEED_MAX = 300;

  // Mirror labelFor() formatting from main.html for tool kinds
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

  function init(container, ws) {
    container.innerHTML = '';
    var feed = document.createElement('div');
    feed.className = 'thinking-feed';
    Object.assign(feed.style, {
      flex: '1', overflowY: 'auto', padding: '10px',
      fontFamily: "'Consolas', 'Cascadia Code', monospace", fontSize: '12px', color: 'var(--text-dim)',
      display: 'flex', flexDirection: 'column', gap: '4px'
    });
    container.appendChild(feed);

    function append(kind, text) {
      var row = document.createElement('div');
      row.className = 'thinking-row ' + kind;
      row.textContent = text;
      row.style.color = kind === 'tool_call' ? 'var(--state-active)' : 'var(--text-muted)';
      feed.appendChild(row);
      while (feed.children.length > FEED_MAX) feed.removeChild(feed.firstChild);
      feed.scrollTop = feed.scrollHeight;
    }

    if (ws) {
      ws.addEventListener('message', function (evt) {
        try {
          var data = JSON.parse(evt.data);
          if (data.type === 'conversation_turn_emitted') {
            var turn = data.turn || data;
            var formatted = formatTurn(turn);
            if (formatted) append(turn.kind, formatted);
          }
        } catch (e) { /* ignore parse errors */ }
      });
    }
  }

  var _exports = { init: init, formatTurn: formatTurn };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.ThinkingPanelMod = _exports;
  }
})();
