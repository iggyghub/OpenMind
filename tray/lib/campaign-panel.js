/* Campaign driver viewer helpers -- pure data shapers for the Documents
 * "Campaign Plans" sub-view (root *.md drivers + docs/adr/*.md).
 * Dual-mode: window.CampaignPanel in the renderer; module.exports for Node tests.
 * IIFE prevents top-level const collisions when loaded via <script src>.
 *
 * No DOM, no IPC. Driver rows match the campaign_drivers_update payload:
 *   { path, name, group, status, updated_at }
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.CampaignPanel = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* One clickable row per driver; the renderer's delegated click handler reads
   * .camp-row[data-path]. */
  function renderDriverList(drivers) {
    return (drivers || []).map(function (d) {
      if (!d || !d.path) return '';
      return '<button class="camp-row" data-path="' + escHtml(d.path) + '" type="button">' +
        '<span class="camp-row-name">' + escHtml(d.name || d.path) + '</span>' +
        (d.status ? '<span class="camp-row-status">' + escHtml(d.status) + '</span>' : '') +
        '</button>';
    }).join('');
  }

  /* Inline: escaped first, then `code`, **bold**, and [text](http(s) url). */
  function inline(text) {
    return escHtml(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  /* Minimal Markdown -> HTML: fenced code, #-headings, -/* bullet lists,
   * paragraphs. Input is escaped before any tag is emitted, so driver files
   * can't inject markup. ponytail: no tables/nesting/numbered lists, add when a
   * driver needs them. */
  function mdToHtml(md) {
    var out = [];
    var para = [];
    var inList = false;
    var inCode = false;
    var code = [];

    function flushPara() {
      if (para.length) out.push('<p>' + inline(para.join(' ')) + '</p>');
      para = [];
    }
    function closeList() {
      if (inList) out.push('</ul>');
      inList = false;
    }

    String(md == null ? '' : md).split(/\r?\n/).forEach(function (line) {
      if (/^\s*```/.test(line)) {
        if (inCode) {
          out.push('<pre><code>' + escHtml(code.join('\n')) + '</code></pre>');
          code = [];
          inCode = false;
        } else {
          flushPara();
          closeList();
          inCode = true;
        }
        return;
      }
      if (inCode) { code.push(line); return; }

      var h = /^(#{1,6})\s+(.*)$/.exec(line);
      if (h) {
        flushPara();
        closeList();
        out.push('<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>');
        return;
      }
      var li = /^\s*[-*]\s+(.*)$/.exec(line);
      if (li) {
        flushPara();
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + inline(li[1]) + '</li>');
        return;
      }
      if (!line.trim()) { flushPara(); closeList(); return; }
      closeList();
      para.push(line.trim());
    });

    if (inCode) out.push('<pre><code>' + escHtml(code.join('\n')) + '</code></pre>');
    flushPara();
    closeList();
    return out.join('');
  }

  return { escHtml: escHtml, renderDriverList: renderDriverList, mdToHtml: mdToHtml };
}));
