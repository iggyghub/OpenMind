/* global document, module */
(function(root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory(root);
  } else if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else {
    root.ActivityLog = factory(root);
  }
})(typeof self !== 'undefined' ? self : this, function(global) {
  const doc = global.document || {};
  let logPane = doc.querySelector('[data-route="log"]') || null;

  function render(data) {
    if (!logPane) return;
    const container = logPane.querySelector('.activity-log-container') || logPane;
    container.innerHTML = '';
    const turns = data.turns || [];
    if (turns.length === 0) {
      container.innerHTML = '<p class="activity-log-empty">No activity recorded yet.</p>';
      return;
    }
    const ul = doc.createElement('ul');
    ul.className = 'activity-log-list';
    for (const turn of turns) {
      const li = doc.createElement('li');
      li.className = 'activity-log-item';
      const meta = doc.createElement('span');
      meta.className = 'activity-log-meta';
      meta.textContent = new Date(turn.ts).toLocaleString();
      li.appendChild(meta);
      const content = doc.createElement('div');
      content.className = 'activity-log-content';
      content.textContent = turn.content.text || JSON.stringify(turn.content);
      li.appendChild(content);
      ul.appendChild(li);
    }
    container.appendChild(ul);
  }

  return { render };
});
