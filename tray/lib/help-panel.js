(function() {
  function escHtml(str) {
    if (typeof str !== 'string') return str;
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function sections(content) {
    if (!content || !content.topics) return [];
    const seen = new Set();
    const result = [];
    for (const topic of content.topics) {
      const sec = topic.section;
      if (!seen.has(sec)) {
        seen.add(sec);
        result.push({ section: sec, topics: [] });
      }
      result.find(s => s.section === sec).topics.push({
        id: topic.id,
        title: topic.title,
        summary: topic.summary
      });
    }
    return result;
  }

  function renderNav(content, activeId) {
    if (!content || !content.topics) return '';
    const secData = sections(content);
    let html = '';
    for (const sec of secData) {
      html += '<div class="help-nav-section">';
      html += '<h4>' + escHtml(sec.section) + '</h4>';
      for (const t of sec.topics) {
        const cls = t.id === activeId ? 'help-nav-item is-active' : 'help-nav-item';
        html += '<button class="' + cls + '" data-topic-id="' + escHtml(t.id) + '">' + escHtml(t.title) + '</button>';
      }
      html += '</div>';
    }
    return html;
  }

  function renderTopic(content, id) {
    if (!content || !content.topics) return '<p>Topic not found.</p>';
    const topic = content.topics.find(t => t.id === id);
    if (!topic) return '<p>Topic not found.</p>';

    let html = '<h2>' + escHtml(topic.title) + '</h2>';
    html += '<p class="help-topic-summary">' + escHtml(topic.summary) + '</p>';
    html += renderBody(topic.body || []);
    html += renderSeeAlso(content, topic.see_also || []);
    return html;
  }

  function renderBody(lines) {
    if (!lines || !lines.length) return '';
    let html = '';
    let listItems = [];

    function flushList() {
      if (listItems.length > 0) {
        html += '<ul>';
        for (const item of listItems) {
          html += '<li>' + item + '</li>';
        }
        html += '</ul>';
        listItems = [];
      }
    }

    for (const line of lines) {
      const escaped = escHtml(line);
      if (escaped.startsWith('- ')) {
        listItems.push(escaped.substring(2));  // consecutive bullets share one <ul>
      } else {
        flushList();
        html += '<p>' + escaped + '</p>';
      }
    }
    flushList();
    return html;
  }

  function renderSeeAlso(content, ids) {
    if (!ids || !ids.length) return '';
    let html = '<div class="help-see-also">';
    for (const id of ids) {
      const topic = content.topics.find(t => t.id === id);
      if (topic) {
        html += '<button class="help-link" data-topic-id="' + escHtml(id) + '">' + escHtml(topic.title) + '</button>';
      }
    }
    html += '</div>';
    return html;
  }

  function searchTopics(content, query) {
    if (!content || !content.topics) return [];
    // Ranking and filtering are delegated to the search registry; this provides the feed.
    return content.topics.map(t => ({
      label: t.title,
      secondary: t.section,
      route: 'help',
      anchor: t.id
    }));
  }

  function renderCapabilities(snapshot, query) {
    if (!snapshot) return '<div class="help-cap-empty">No capabilities available.</div>';
    const plugins = snapshot.plugins || snapshot || [];
    const q = query && query.trim().length > 0 ? query.toLowerCase() : null;
    const sorted = [...plugins].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    let html = '';
    let pCount = 0, tCount = 0;
    for (const plugin of sorted) {
      const isDisabled = plugin.status !== 'active' || plugin.enabled === false;
      let tools = (plugin.tools || []).filter(t => {
        if (!q) return true;
        return (t.name || '').toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q);
      });
      if (q && (plugin.name || '').toLowerCase().includes(q)) {
        tools = plugin.tools || [];  // a plugin-name match keeps all its tools
      }
      if (tools.length === 0) continue;
      tools = [...tools].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
      pCount++;
      tCount += tools.length;
      let cls = 'help-cap-plugin';
      if (isDisabled) cls += ' is-disabled';
      html += '<section class="' + cls + '">';
      html += '<h3>' + escHtml(plugin.name) + (isDisabled ? ' <span class="help-cap-status">(disabled)</span>' : '') + '</h3>';
      if (plugin.capabilities && plugin.capabilities.length) {
        html += '<div class="help-cap-tags">';
        for (const c of plugin.capabilities) html += '<span class="help-cap-tag">' + escHtml(c) + '</span>';
        html += '</div>';
      }
      for (const t of tools) {
        html += '<div class="help-cap-tool">';
        html += '<div class="help-cap-name">' + escHtml(t.name) + '</div>';
        html += '<div class="help-cap-desc">' + escHtml(t.description) + '</div>';
        if (t.supersedes) html += '<div class="help-cap-supersedes">Supersedes: ' + escHtml(t.supersedes) + '</div>';
        html += '</div>';
      }
      html += '</section>';
    }
    if (pCount === 0) return '<div class="help-cap-empty">No capabilities available.</div>';
    return '<div class="help-cap-index"><h2>Capabilities Index</h2><p class="help-cap-summary">' + pCount + ' plugin(s), ' + tCount + ' tool(s) shown.</p>' + html + '</div>';
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { escHtml, sections, renderNav, renderTopic, searchTopics, renderCapabilities };
  } else if (typeof window !== 'undefined') {
    window.HelpPanel = { escHtml, sections, renderNav, renderTopic, searchTopics, renderCapabilities };
  }
})();
