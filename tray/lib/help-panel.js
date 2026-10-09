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
        flushList();
        listItems.push(escaped.substring(2));
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

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { escHtml, sections, renderNav, renderTopic, searchTopics };
  } else if (typeof window !== 'undefined') {
    window.HelpPanel = { escHtml, sections, renderNav, renderTopic, searchTopics };
  }
})();
