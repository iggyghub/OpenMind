/* Activity Log panel helpers -- S26 (#879), decision #46.
 * Dual-mode: window.ActivityLogMod in the renderer (main.html loads this via
 * <script src>, matching trading-panel.js's convention), module.exports for
 * Node tests. No jsdom in this repo's jest config -- tests inject a fake
 * `document`, matching every other dual-mode lib here.
 *
 * Two render destinations share this module: the top-level "Log" nav tab
 * (the full, unfiltered activity stream) and the Trading pane's own Activity
 * section (server-filtered to source:"trading" -- see cerebral/main.py's
 * _handle_activity_poll). Both are populated by the same `activity_log_data`
 * broadcast; the caller (main.html) distinguishes them by `data.source`
 * (falsy -> Log tab, "trading" -> Trading pane) and passes the right
 * container into renderActivityLog.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.ActivityLogMod = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {

/**
 * Prepares an Activity Log mount with a loading placeholder. Does not
 * itself request data -- the caller sends `activity_poll` via its own
 * sendEvent() after calling this, matching initTradingPanel's contract.
 * @param {string} [mountId] - defaults to 'activity-log-mount'
 */
function initActivityLog(mountId) {
  const mount = document.getElementById(mountId || 'activity-log-mount');
  if (!mount) return;
  mount.innerHTML = '<div class="activity-log-loading">Loading activity…</div>';
}

/**
 * Renders an `activity_log_data` broadcast's turns into the mount. The DOM
 * lookup happens HERE, on every call -- not cached at module-load time
 * (the S26 self_dev PR's original bug: a module-level `querySelector` run
 * once at require() time means render() becomes a permanent no-op if the
 * pane didn't exist in the DOM yet when the script first loaded).
 * @param {Object} data - { turns: [...] } from cerebral/main.py's
 *   _handle_activity_poll / activity_turn_emitted
 * @param {HTMLElement} [container] - defaults to #activity-log-mount
 */
function renderActivityLog(data, container) {
  const mount = container || document.getElementById('activity-log-mount');
  if (!mount) return;
  const turns = (data && data.turns) || [];
  if (turns.length === 0) {
    mount.innerHTML = '<p class="activity-log-empty">No activity recorded yet.</p>';
    return;
  }
  mount.innerHTML = '';
  const ul = document.createElement('ul');
  ul.className = 'activity-log-list';
  for (const turn of turns) {
    const li = document.createElement('li');
    li.className = 'activity-log-item';
    const meta = document.createElement('span');
    meta.className = 'activity-log-meta';
    meta.textContent = turn.ts ? new Date(turn.ts).toLocaleString() : '';
    li.appendChild(meta);
    const content = document.createElement('div');
    content.className = 'activity-log-content';
    const c = turn.content || {};
    content.textContent = c.summary || c.text || JSON.stringify(c);
    li.appendChild(content);
    ul.appendChild(li);
  }
  mount.appendChild(ul);
}

return { initActivityLog, renderActivityLog };

}));
