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

  function init(container) {
    container.innerHTML = '';
    var feed = document.createElement('div');
    feed.className = 'thinking-feed';
    Object.assign(feed.style, {
      flex: '1', overflowY: 'auto', padding: '10px',
      fontFamily: "'Consolas', 'Cascadia Code', monospace", fontSize: '12px', color: 'var(--text-dim)',
      display: 'flex', flexDirection: 'column', gap: '4px'
    });
    container.appendChild(feed);
    return feed;
  }

  function appendTurn(container, turn) {
    var feed = container.querySelector('.thinking-feed');
    if (!feed) return null;
    var formatted = formatTurn(turn);
    if (!formatted) return null;
    var row = document.createElement('div');
    row.className = 'thinking-row ' + (turn.kind || '');
    row.textContent = formatted;
    row.style.color = (turn.kind === 'tool_call') ? 'var(--state-active)' : 'var(--text-muted)';
    feed.appendChild(row);
    while (feed.children.length > FEED_MAX) feed.removeChild(feed.firstChild);
    feed.scrollTop = feed.scrollHeight;
    return row;
  }

  var _exports = { init: init, appendTurn: appendTurn, formatTurn: formatTurn };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.ThinkingPanelMod = _exports;
  }
})();
