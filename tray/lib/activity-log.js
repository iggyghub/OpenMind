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
  // activity_turn_emitted arrives as {turn} normalized to a one-item `turns`
  // with `append: true`: add it to what's shown instead of replacing it all.
  const incoming = (data && data.turns) || [];
  const turns = data && data.append ? (mount._turns || []).concat(incoming) : incoming;
  mount._turns = turns;
  if (turns.length === 0) {
    mount.innerHTML = '<p class="activity-log-empty">No activity recorded yet.</p>';
    return;
  }
  mount.innerHTML = '';
  const ul = document.createElement('ul');
  ul.className = 'activity-log-list';
  for (const run of groupRuns(turns.slice().reverse())) {  // newest first
    if (run.length < 3) { run.forEach((t) => ul.appendChild(itemEl(t))); continue; }
    // 3+ identical entries in a row (mostly "Strategy idea rejected") fold
    // into one expandable line instead of a wall of repeats.
    const li = document.createElement('li');
    li.className = 'activity-log-group';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    const d = describeTurn(run[0].content);
    summary.textContent = formatTs(run[0].ts) + '  ·  ' + run.length + ' × ' + d.title;
    details.appendChild(summary);
    const inner = document.createElement('ul');
    inner.className = 'activity-log-list';
    run.forEach((t) => inner.appendChild(itemEl(t)));
    details.appendChild(inner);
    li.appendChild(details);
    ul.appendChild(li);
  }
  mount.appendChild(ul);
}

// `ts` is SQLite CURRENT_TIMESTAMP: UTC with no zone marker, which Date
// would otherwise read as local time.
function formatTs(ts) {
  if (!ts) return '';
  const iso = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(ts) ? ts.replace(' ', 'T') + 'Z' : ts;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; }
}

// One readable line per known entry shape; never raw JSON.
function describeTurn(c) {
  c = c || {};
  if (c.source === 'discovery') {
    return { title: 'Strategy idea ' + (c.status || 'checked'), tag: host(c.idea_url),
      detail: c.reason === 'accepted by judge' ? '' : (c.reason || '') };
  }
  if (c.pr_url) {
    const num = (c.pr_url.match(/\/pull\/(\d+)/) || [])[1];
    return { title: (c.summary || 'Self-dev PR').replace(c.pr_url, num ? '#' + num : 'PR'), tag: '', detail: '' };
  }
  if (c.summary || c.text) return { title: c.summary || c.text, tag: '', detail: '' };
  const rest = Object.keys(c).filter((k) => k !== 'source')
    .map((k) => k + ': ' + (typeof c[k] === 'object' ? JSON.stringify(c[k]) : c[k]));
  return { title: rest.join('  ·  ') || '(empty entry)', tag: '', detail: '' };
}

function groupRuns(turns) {
  const runs = [];
  for (const t of turns) {
    const last = runs[runs.length - 1];
    if (last && describeTurn(last[0].content).title === describeTurn(t.content).title) last.push(t);
    else runs.push([t]);
  }
  return runs;
}

function itemEl(turn) {
  const c = turn.content || {};
  const d = describeTurn(c);
  const li = document.createElement('li');
  li.className = 'activity-log-item';
  const meta = document.createElement('span');
  meta.className = 'activity-log-meta';
  meta.textContent = formatTs(turn.ts) + (c.source ? '  ·  ' + c.source : '');
  li.appendChild(meta);
  const content = document.createElement('div');
  content.className = 'activity-log-content';
  content.textContent = d.title + (d.tag ? '  —  ' + d.tag : '');
  li.appendChild(content);
  if (d.detail) {
    const detail = document.createElement('div');
    detail.className = 'activity-log-detail';
    detail.textContent = d.detail;
    li.appendChild(detail);
  }
  return li;
}

return { initActivityLog, renderActivityLog, describeTurn, formatTs };

}));
