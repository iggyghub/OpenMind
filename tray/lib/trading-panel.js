/* Trading panel helpers -- issue #848 (S6-S9).
 * Dual-mode: window.TradingPanelMod in the renderer (main.html loads this
 * via <script src>, matching thinking-panel.js's convention -- there is no
 * `export`/`import` here because a plain <script> tag can't use ES module
 * syntax), module.exports for Node tests.
 *
 * No WS/sendEvent logic lives in this file (that was the S9 bug: it called
 * window.sendEvent/listened on window.addEventListener('message', ...),
 * neither of which anything in this app ever sends -- the real transport is
 * ws-bridge.js's onMessage callback, routed through main.html's own
 * handleEvent(event) switch on event.type, exactly like every other panel).
 * initTradingPanel() only prepares the mount and asks the caller to poll;
 * renderTradingUpdate() is what main.html calls from its switch case.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.TradingPanelMod = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {

/**
 * Prepares the Trading Panel mount with a loading placeholder. Does not
 * itself request data -- the caller (main.html) sends the initial
 * `trading_poll` event via its own sendEvent() after calling this, and
 * routes the `trading_update` response to renderTradingUpdate() the same
 * way it routes every other broadcast.
 */
function initTradingPanel() {
  const mount = document.getElementById('trading-panel-mount');
  if (!mount) return;
  mount.innerHTML = '<div style="padding:16px; color:var(--text-muted); text-align:center;">Loading trading state…</div>';
}

/**
 * Discovery control block (S31/#896) -- start/stop + optional duration,
 * rendered above the strategy list/empty-state either way (discovery is
 * independent of whether any strategy exists yet). Pure string builder,
 * matching this file's own convention for the other render helpers.
 * @param {Object} [discovery] - { enabled, stop_at, queries, interval }
 *   from cerebral/main.py's _trading_broadcast(); undefined/null renders
 *   as stopped (a caller/test that hasn't wired discovery yet still works).
 */
function _renderDiscoveryControl(discovery) {
  const running = !!(discovery && discovery.enabled);
  let statusText = 'Stopped';
  if (running) {
    statusText = 'Running indefinitely';
    if (discovery.stop_at) {
      const stopDate = new Date(discovery.stop_at);
      if (!isNaN(stopDate.getTime())) statusText = 'Running -- stops ' + stopDate.toLocaleString();
    }
  }
  return `
    <div class="discovery-control">
      <h3>Autonomous Discovery</h3>
      <div class="discovery-control-row">
        <button class="discovery-start-btn" ${running ? 'disabled' : ''}>Start</button>
        <button class="discovery-stop-btn" ${running ? '' : 'disabled'}>Stop</button>
        <span class="discovery-status">${statusText}</span>
      </div>
      <div class="discovery-control-row">
        <label for="discovery-duration-input">Duration (hours, blank = indefinite)</label>
        <input type="number" class="discovery-duration-input" min="0" step="0.5" placeholder="e.g. 2" ${running ? 'disabled' : ''}>
      </div>
    </div>
  `;
}

/**
 * Wires the discovery control's Start/Stop buttons. Sends a trading_poll
 * right after each call_tool so the panel reflects the new state without
 * waiting for the next natural broadcast -- same pattern other action
 * buttons in this app use to refresh themselves post-action.
 */
function _wireDiscoveryControl(mount, sendEventFn) {
  const startBtn = mount.querySelector('.discovery-start-btn');
  const stopBtn = mount.querySelector('.discovery-stop-btn');
  const durInput = mount.querySelector('.discovery-duration-input');
  if (startBtn) {
    startBtn.addEventListener('click', () => {
      if (!sendEventFn) return;
      const hours = durInput && durInput.value !== '' ? parseFloat(durInput.value) : null;
      sendEventFn(buildStartDiscoveryEvent(hours));
      sendEventFn({ type: 'trading_poll' });
    });
  }
  if (stopBtn) {
    stopBtn.addEventListener('click', () => {
      if (!sendEventFn) return;
      sendEventFn(buildStopDiscoveryEvent());
      sendEventFn({ type: 'trading_poll' });
    });
  }
}

/**
 * Builds the `start_discovery` call_tool event (S31/#896). durationHours
 * of null/undefined/NaN omits duration_hours entirely -- start_discovery
 * treats that as "run indefinitely," matching the tool's own convention.
 */
function buildStartDiscoveryEvent(durationHours) {
  const args = {};
  if (typeof durationHours === 'number' && !isNaN(durationHours)) {
    args.duration_hours = durationHours;
  }
  return { type: 'call_tool', data: { name: 'start_discovery', args: args } };
}

function buildStopDiscoveryEvent() {
  return { type: 'call_tool', data: { name: 'stop_discovery', args: {} } };
}

/**
 * Books section (2026-08-26, own sub-tab since 2026-08-27): multi-file
 * upload -- Felix reads each book in full and pulls testable strategy
 * claims out of it, dispatching each through the same judge/screen/
 * gauntlet pipeline web-sourced ideas use.
 * @param {Array} [books] - [{id, title, filename, status, total_chunks,
 *   processed_chunks, strategies_found, error_message, valid_strategies}]
 *   from cerebral/main.py's _trading_broadcast(). strategies_found is
 *   every gauntlet DISPATCH attempt (pass or fail -- a single accepted
 *   claim fans out to up to candidate_limit tickers, each counted);
 *   valid_strategies is the real validated/persisted list, usually much
 *   smaller -- see books.py's list_validated_strategies.
 * @param {string} [booksModel] - friendly label of the model currently
 *   mapped to the 'books' task, or falsy if unknown.
 * @param {Set<number>} [expandedIds] - book ids whose valid-strategies
 *   list should render open, not collapsed (persisted on the mount
 *   across re-renders -- see renderBooksPanel).
 */
function _renderBookRow(b, expanded) {
  const pct = b.total_chunks > 0 ? Math.round((b.processed_chunks / b.total_chunks) * 100) : 0;
  const statusLabel = b.status === 'error' ? 'Error: ' + (b.error_message || 'unknown') : b.status;
  const canStop = b.status === 'processing' || b.status === 'queued';
  const validList = b.valid_strategies || [];
  const isOpen = expanded.has(b.id);
  const validListHtml = validList.length ? `
    <ul class="book-valid-list" ${isOpen ? '' : 'hidden'}>
      ${validList.map((s) => `
        <li class="book-valid-item">
          <span class="book-valid-symbol">${s.symbol}</span>
          <span class="book-valid-hypothesis">${s.hypothesis}</span>
          <span class="book-valid-chapter">(${s.chapter})</span>
        </li>
      `).join('')}
    </ul>
  ` : '';
  return `
    <div class="book-row" data-status="${b.status}" data-book-id="${b.id}">
      <div class="book-row-title">${b.title}</div>
      <div class="book-row-meta">
        <span class="book-status book-status-${b.status}">${statusLabel}</span>
        ${b.status === 'processing' ? `<span class="book-progress-text">${b.processed_chunks}/${b.total_chunks} chunks</span>` : ''}
        <span class="book-dispatch-count">${b.strategies_found} dispatch${b.strategies_found === 1 ? '' : 'es'}</span>
        <button class="book-valid-toggle" type="button" data-book-id="${b.id}" ${validList.length ? '' : 'disabled'}>
          ${validList.length} valid strateg${validList.length === 1 ? 'y' : 'ies'}
        </button>
      </div>
      ${b.status === 'processing' ? `<div class="book-progress-bar"><div class="book-progress-fill" style="width:${pct}%"></div></div>` : ''}
      ${validListHtml}
      <div class="book-row-actions">
        ${canStop ? `<button class="book-stop-btn" type="button" data-book-id="${b.id}">Stop</button>` : ''}
        ${b.status === 'stopped' ? `<button class="book-resume-btn" type="button" data-book-id="${b.id}">Resume</button>` : ''}
        <button class="book-retry-btn" type="button" data-book-id="${b.id}">Redo</button>
        <button class="book-delete-btn" type="button" data-book-id="${b.id}">Delete</button>
      </div>
    </div>
  `;
}

function _renderBooksSection(books, booksModel, expandedIds) {
  const expanded = expandedIds || new Set();
  const all = books || [];
  // Finished books collapse into their own <details> (2026-08-28) -- once
  // a handful of books are done the flat list buries whatever's still
  // active/needs attention. <details>/<summary> is native, collapsed by
  // default, no JS toggle wiring needed for the open/close behavior itself.
  const activeBooks = all.filter((b) => b.status !== 'done');
  const doneBooks = all.filter((b) => b.status === 'done');

  const activeHtml = activeBooks.map((b) => _renderBookRow(b, expanded)).join('');
  const doneSection = doneBooks.length ? `
    <details class="books-done-section">
      <summary>${doneBooks.length} finished book${doneBooks.length === 1 ? '' : 's'}</summary>
      <div class="books-list">${doneBooks.map((b) => _renderBookRow(b, expanded)).join('')}</div>
    </details>
  ` : '';

  return `
    <div class="books-section">
      <h3>Books ${booksModel ? `<span class="books-reading-model">reading with ${booksModel}</span>` : ''}</h3>
      <div class="books-upload-row">
        <input type="file" class="books-file-input" multiple accept=".pdf,.epub,.mobi,.azw,.azw3,.docx,.doc,.odt,.rtf,.txt,.md">
        <span class="books-upload-hint">Upload several at once -- each reads in full and processes in the background.</span>
      </div>
      ${all.length === 0 ? '<div class="books-empty">No books uploaded yet.</div>' : ''}
      <div class="books-list">${activeHtml}</div>
      ${doneSection}
    </div>
  `;
}

/**
 * Builds the `upload_book` call_tool event for one file. Pure so the
 * event shape is directly testable without a real FileReader (this repo
 * has no jsdom -- see trading-panel.test.js's header comment).
 * @param {string} filename
 * @param {string} dataBase64 - base64-encoded file bytes
 * @param {string} [title] - defaults (server-side) to filename without extension
 */
function buildUploadBookEvent(filename, dataBase64, title) {
  const args = { filename: filename, data_base64: dataBase64 };
  if (title) args.title = title;
  return { type: 'call_tool', data: { name: 'upload_book', args: args } };
}

/** Cancels a book's in-progress ingestion, freezing its progress in place. */
function buildStopBookEvent(bookId) {
  return { type: 'call_tool', data: { name: 'stop_book', args: { book_id: bookId } } };
}

/** Redoes a book's ingestion from scratch (re-extracts + re-chunks the stored file). */
function buildRetryBookEvent(bookId) {
  return { type: 'call_tool', data: { name: 'retry_book', args: { book_id: bookId } } };
}

/** Continues a stopped book from the exact chunk it stopped at (2026-08-28) -- not a redo. */
function buildResumeBookEvent(bookId) {
  return { type: 'call_tool', data: { name: 'resume_book', args: { book_id: bookId } } };
}

/** Removes a book's record and stored file (strategies already dispatched are kept). */
function buildDeleteBookEvent(bookId) {
  return { type: 'call_tool', data: { name: 'delete_book', args: { book_id: bookId } } };
}

/** Halts a strategy's autonomous dispatch manually. */
function buildHaltStrategyEvent(strategyId) {
  return { type: 'call_tool', data: { name: 'halt_strategy', args: { strategy_id: strategyId } } };
}

/** Reverses a manual or automatic halt, resuming at 'paper' status. */
function buildResumeStrategyEvent(strategyId) {
  return { type: 'call_tool', data: { name: 'resume_strategy', args: { strategy_id: strategyId } } };
}

/**
 * Wires the multi-file input (reads each selected file as base64 and fires
 * one upload_book call per file, then polls once so the new queued row
 * appears without waiting for the first background progress tick) and the
 * per-row Stop/Redo/Delete buttons (event-delegated off .books-section,
 * not just .books-list -- finished books live in a second .books-list
 * nested inside the <details> collapsible, a sibling of the active one,
 * so delegating off a single .books-list would miss clicks in there).
 */
function _wireBooksSection(mount, sendEventFn) {
  const input = mount.querySelector('.books-file-input');
  if (input && sendEventFn) {
    input.addEventListener('change', () => {
      const files = Array.from(input.files || []);
      files.forEach((file) => {
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = String(reader.result).split(',').pop(); // strip data: URL prefix
          sendEventFn(buildUploadBookEvent(file.name, base64));
          sendEventFn({ type: 'trading_poll' });
        };
        reader.readAsDataURL(file);
      });
      input.value = ''; // allow re-selecting the same file(s) later
    });
  }

  const list = mount.querySelector('.books-section');
  if (!list) return;
  list.addEventListener('click', (e) => {
    const validBtn = e.target.closest('.book-valid-toggle');
    if (validBtn && !validBtn.disabled) {
      const bookId = parseInt(validBtn.dataset.bookId, 10);
      const listEl = validBtn.closest('.book-row').querySelector('.book-valid-list');
      if (listEl) {
        listEl.hidden = !listEl.hidden;
        if (mount._expandedBookIds) {
          if (listEl.hidden) mount._expandedBookIds.delete(bookId);
          else mount._expandedBookIds.add(bookId);
        }
      }
      return;
    }
    if (!sendEventFn) return;
    const stopBtn = e.target.closest('.book-stop-btn');
    if (stopBtn) {
      sendEventFn(buildStopBookEvent(parseInt(stopBtn.dataset.bookId, 10)));
      return;
    }
    const resumeBtn = e.target.closest('.book-resume-btn');
    if (resumeBtn) {
      sendEventFn(buildResumeBookEvent(parseInt(resumeBtn.dataset.bookId, 10)));
      return;
    }
    const retryBtn = e.target.closest('.book-retry-btn');
    if (retryBtn) {
      sendEventFn(buildRetryBookEvent(parseInt(retryBtn.dataset.bookId, 10)));
      return;
    }
    const deleteBtn = e.target.closest('.book-delete-btn');
    if (deleteBtn) {
      const row = deleteBtn.closest('.book-row');
      const title = row ? row.querySelector('.book-row-title').textContent : 'this book';
      if (window.confirm(`Delete "${title}"? This removes its record and stored file -- strategies it already produced are kept.`)) {
        sendEventFn(buildDeleteBookEvent(parseInt(deleteBtn.dataset.bookId, 10)));
      }
    }
  });
}

/**
 * Renders the Books sub-tab (own tab since 2026-08-27, previously embedded
 * atop the Strategies sub-tab) from the same `trading_update` payload
 * renderTradingUpdate already receives -- no separate broadcast/poll.
 * @param {Object} data - { books, books_model } from _trading_broadcast()
 * @param {HTMLElement} [container] - defaults to #books-panel-mount
 * @param {Function} [sendEventFn] - see renderTradingUpdate's own doc
 */
function renderBooksPanel(data, container, sendEventFn) {
  const mount = container || document.getElementById('books-panel-mount');
  if (!mount) return;
  if (!mount._expandedBookIds) mount._expandedBookIds = new Set();
  _injectTradingPanelStyles();
  mount.innerHTML = _renderBooksSection(data && data.books, data && data.books_model, mount._expandedBookIds);
  _wireBooksSection(mount, sendEventFn);
}

/**
 * Renders a `trading_update` broadcast's data into the mount.
 * @param {Object} data - { positions: [...], alerts: [...] } from
 *   cerebral/main.py's _trading_broadcast()
 * @param {HTMLElement} [container] - defaults to #trading-panel-mount
 * @param {Function} [sendEventFn] - the caller's own sendEvent (main.html's
 *   local function, backed by ws-bridge.js) -- passed in explicitly rather
 *   than reached for as window.sendEvent, which is the exact S9 bug this
 *   file's own header comment warns about. Optional so a caller that never
 *   needs the edit box to actually save (e.g. a render-only test) doesn't
 *   have to supply one -- the Save button just does nothing without it.
 */
function renderTradingUpdate(data, container, sendEventFn) {
  const mount = container || document.getElementById('trading-panel-mount');
  if (!mount) return;

  // S31 (#896): rendered (and wired) in both branches below -- discovery
  // control is independent of whether any strategy exists yet, so it must
  // not disappear behind the empty-state message. Styles injected here,
  // unconditionally, so both branches have them (previously only the
  // non-empty branch injected any styles at all).
  const discoveryHtml = _renderDiscoveryControl(data && data.discovery);
  _injectTradingPanelStyles();

  if (!data || !data.positions || data.positions.length === 0) {
    mount.innerHTML = discoveryHtml + '<div style="padding:16px; color:var(--text-muted); text-align:center;">No active strategies. Create one via the Scheduler or Strategy Gauntlet.</div>';
    _wireDiscoveryControl(mount, sendEventFn);
    return;
  }

  // S19: maintain interactive state on the mount element
  if (!mount._strategyState) {
    mount._strategyState = { selectedIdx: 0, strategies: data.positions, alerts: data.alerts || [] };
  } else {
    mount._strategyState.strategies = data.positions;
    mount._strategyState.alerts = data.alerts || [];
  }
  const state = mount._strategyState;
  const strategy = state.strategies[state.selectedIdx];

  mount.innerHTML = discoveryHtml + `
    <div class="trading-panel-layout">
      <div class="strategy-list">
        <h3>Strategies</h3>
        <ul>
          ${state.strategies.map((s, i) => {
            const trades = s.live_trades || 0;
            return `
            <li class="${i === state.selectedIdx ? 'selected' : ''}" data-idx="${i}">
              ${s.name} <span class="status-badge">${s.status.toUpperCase()}</span> <span class="trade-count-badge">${trades} trade${trades === 1 ? '' : 's'}</span>
            </li>
          `;
          }).join('')}
        </ul>
      </div>
      <div class="strategy-detail">
        ${strategy ? `
          <div class="detail-header">
            <h2>${strategy.name}</h2>
            <div class="version-badge">v${strategy.version ?? '0'}</div>
          </div>
          <div class="provenance-box">${strategy.provenance || 'No lineage recorded.'}</div>
          <div class="edit-box">
            <h3>Edit Strategy Code</h3>
            <textarea class="strategy-code-editor" rows="12" spellcheck="false">${strategy.code || ''}</textarea>
            <button class="save-strategy-btn">Save Changes</button>
            <button class="halt-resume-btn" id="halt-resume-btn" style="margin-top: 8px; padding: 6px 12px; background: #e74c3c; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 500;">${strategy.status === 'halted' ? 'Resume' : 'Halt'}</button>
          </div>
          <div class="fill-list">
             <h3>Recent Fills</h3>
             <table class="fills-table">
               <thead><tr><th>Symbol</th><th>Side</th><th>PnL</th><th>Phase</th></tr></thead>
               <tbody>
                 ${(strategy.recent_fills || []).slice(0, 5).map(f => `
                   <tr><td>${f.symbol}</td><td>${f.side.toUpperCase()}</td><td>${f.pnl.toFixed(2)}</td><td>${(f.phase || 'paper').toUpperCase()}</td></tr>
                 `).join("") || '<tr><td colspan="4">No fills yet</td></tr>'}
               </tbody>
             </table>
          </div>
          <div class="alerts-box">
             <h3>Alerts</h3>
             <ul>${(state.alerts || []).slice(0, 5).map(a => `<li class="alert-${a.severity}">[${a.severity.toUpperCase()}] ${a.event_type}: ${a.message}</li>`).join("") || '<li>No alerts</li>'}</ul>
          </div>
        ` : '<div style="padding:16px;">Select a strategy.</div>'}
      </div>
    </div>
  `;

  _wireDiscoveryControl(mount, sendEventFn);

  // Wire list selection
  mount.querySelectorAll('.strategy-list li').forEach(li => {
    li.addEventListener('click', () => {
      state.selectedIdx = parseInt(li.dataset.idx, 10);
      renderTradingUpdate(data, mount, sendEventFn);
    });
  });

  // Wire edit save button
  const saveBtn = mount.querySelector('.save-strategy-btn');
  const haltResumeBtn = mount.querySelector('#halt-resume-btn');
  const textarea = mount.querySelector('.strategy-code-editor');
  if (saveBtn && textarea && strategy) {
    saveBtn.addEventListener('click', () => {
      if (sendEventFn) sendEventFn(buildStrategyEditEvent(strategy, textarea.value));
    });
  }
  if (haltResumeBtn && sendEventFn) {
    haltResumeBtn.addEventListener('click', () => {
      if (strategy.status !== 'halted' && !window.confirm("Halt this strategy's autonomous dispatch?")) return;
      sendEventFn(strategy.status === 'halted' ? buildResumeStrategyEvent(strategy.name) : buildHaltStrategyEvent(strategy.name));
    });
  }
}

/**
 * Idempotent style injection for renderTradingUpdate's markup, including
 * the discovery control (S31/#896). Hoisted out to its own function and
 * called unconditionally near the top of renderTradingUpdate -- previously
 * this only ran on the non-empty-positions path (dead code on the
 * empty-state branch, harmless when that branch had no styled markup of
 * its own, but the discovery control now renders on BOTH branches and
 * needs its styles present either way.
 */
function _injectTradingPanelStyles() {
  const styleId = "trading-panel-v2-styles";
  if (document.getElementById(styleId)) return;
  const style = document.createElement("style");
  style.id = styleId;
  style.textContent = `
    .trading-panel-layout { display: flex; gap: 16px; font-family: sans-serif; }
    .strategy-list { width: 220px; border-right: 1px solid #eee; padding-right: 12px; }
    .strategy-list ul { list-style: none; padding: 0; margin: 0; }
    .strategy-list li { padding: 8px; cursor: pointer; border-radius: 4px; margin-bottom: 4px; color: #333; }
    .strategy-list li.selected { background: #e3f2fd; font-weight: bold; color: #0d47a1; }
    .strategy-list li:hover { background: #f5f5f5; }
    .status-badge { font-size: 0.7em; padding: 2px 5px; border-radius: 3px; background: #eee; margin-left: 6px; }
    .strategy-detail { flex: 1; }
    .detail-header { display: flex; align-items: center; gap: 10px; border-bottom: 1px solid #eee; padding-bottom: 8px; margin-bottom: 12px; }
    .version-badge { background: #f1f1f1; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; font-weight: bold; }
    .provenance-box { background: #f8f9fa; padding: 10px; border-radius: 4px; margin-bottom: 12px; font-size: 0.9em; white-space: pre-wrap; border-left: 3px solid #3498db; }
    .edit-box { margin-top: 12px; }
    .strategy-code-editor { width: 100%; font-family: monospace; font-size: 0.9em; padding: 8px; border: 1px solid #ccc; border-radius: 4px; resize: vertical; box-sizing: border-box; }
    .save-strategy-btn { margin-top: 8px; padding: 6px 12px; background: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 500; }
    .save-strategy-btn:hover { background: #2980b9; }
    .halt-resume-btn { margin-top: 8px; padding: 6px 12px; background: #e74c3c; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 500; }
    .trade-count-badge { font-size: 0.7em; padding: 2px 5px; border-radius: 3px; background: #e0e0e0; margin-left: 6px; }
    .fill-list, .alerts-box { margin-top: 16px; }
    .fills-table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
    .fills-table th, .fills-table td { padding: 4px 6px; border: 1px solid #eee; text-align: left; }
    .alerts-box ul { list-style: none; padding: 0; }
    .alerts-box li { padding: 4px 0; border-bottom: 1px solid #f0f0f0; font-size: 0.9em; }
    .alert-critical { color: #e74c3c; }
    .alert-warning { color: #f39c12; }
    .alert-info { color: #3498db; }
    .discovery-control { margin-bottom: 16px; padding: 12px; background: var(--bg-elev, #f8f9fa); border-radius: 6px; border: 1px solid var(--border, #eee); font-family: sans-serif; }
    .discovery-control h3 { margin: 0 0 8px; font-size: 14px; color: var(--text, #333); }
    .discovery-control-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
    .discovery-control-row:last-child { margin-bottom: 0; }
    .discovery-control label { color: var(--text-muted, #555); font-size: 0.9em; }
    .discovery-start-btn, .discovery-stop-btn { padding: 4px 10px; border: none; border-radius: 4px; cursor: pointer; font-weight: 500; color: #fff; }
    .discovery-start-btn { background: #2ecc71; }
    .discovery-stop-btn { background: #e74c3c; }
    .discovery-start-btn:disabled, .discovery-stop-btn:disabled { background: var(--border, #ccc); color: var(--text-muted, #888); cursor: default; }
    .discovery-status { font-size: 12px; color: var(--text-muted, #555); }
    .discovery-duration-input { width: 80px; padding: 3px 6px; border: 1px solid var(--border, #ccc); border-radius: 3px; background: var(--bg, #fff); color: var(--text, #333); }
    .books-section { margin-bottom: 16px; padding: 12px; background: var(--bg-elev, #f8f9fa); border-radius: 6px; border: 1px solid var(--border, #eee); font-family: sans-serif; }
    .books-section h3 { margin: 0 0 8px; font-size: 14px; color: var(--text, #333); }
    .books-reading-model { font-size: 0.75em; font-weight: normal; color: var(--text-muted, #777); margin-left: 6px; }
    .books-upload-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
    .books-upload-hint { font-size: 0.85em; color: var(--text-muted, #777); }
    .books-list { display: flex; flex-direction: column; gap: 8px; }
    .books-empty { color: var(--text-muted, #888); font-size: 0.9em; padding: 4px 0; }
    .books-done-section { margin-top: 10px; }
    .books-done-section > summary { cursor: pointer; font-size: 0.85em; color: var(--text-muted, #666); padding: 4px 0; user-select: none; }
    .books-done-section > .books-list { margin-top: 8px; }
    .book-row { padding: 8px 10px; background: var(--bg, #fff); border: 1px solid var(--border, #eee); border-radius: 4px; }
    .book-row-title { font-weight: 500; color: var(--text, #333); margin-bottom: 4px; }
    .book-row-meta { display: flex; gap: 10px; align-items: center; font-size: 0.85em; color: var(--text-muted, #666); flex-wrap: wrap; }
    .book-status { padding: 1px 6px; border-radius: 3px; background: #eee; text-transform: capitalize; }
    .book-status-done { background: #d4edda; color: #155724; }
    .book-status-processing { background: #fff3cd; color: #856404; }
    .book-status-error { background: #f8d7da; color: #721c24; }
    .book-status-queued { background: #e2e3e5; color: #383d41; }
    .book-progress-bar { margin-top: 6px; height: 5px; border-radius: 3px; background: var(--border, #eee); overflow: hidden; }
    .book-progress-fill { height: 100%; background: #3498db; transition: width 0.3s ease; }
    .book-valid-toggle { font-size: 0.85em; background: none; border: 1px solid var(--border, #ccc); border-radius: 3px; padding: 1px 6px; cursor: pointer; color: var(--text, #333); }
    .book-valid-toggle:disabled { opacity: 0.5; cursor: default; }
    .book-valid-list { margin: 8px 0 0; padding: 0; list-style: none; border-top: 1px solid var(--border, #eee); }
    .book-valid-item { display: flex; gap: 8px; align-items: baseline; padding: 6px 0; font-size: 0.85em; border-bottom: 1px solid var(--border, #f0f0f0); }
    .book-valid-symbol { font-weight: 600; color: var(--text, #333); }
    .book-valid-hypothesis { color: var(--text, #333); flex: 1; }
    .book-valid-chapter { color: var(--text-muted, #888); font-size: 0.9em; }
  `;
  document.head.appendChild(style);
}

/**
 * Builds the `strategy_edit` event the Save button sends (S19/#864),
 * routed through main.html's real sendEvent -> handleEvent switch to
 * S17's edit_strategy tool. A pure function, not inlined in the click
 * handler, so the event shape is directly testable without simulating a
 * DOM click (this file has no jsdom dependency to do that with -- see
 * tray/tests/trading-panel.test.js's own header comment).
 */
function buildStrategyEditEvent(strategy, code) {
  return {
    type: 'strategy_edit',
    data: {
      strategy_name: strategy.name,
      code: code,
      version: strategy.version,
    },
  };
}

/**
 * Builds the `call_tool` event the create-strategy form sends (Trading
 * pane follow-up, #864). Reuses the existing generic call_tool WS route
 * (cerebral/main.py's `elif t == "call_tool"`, already ACL/consent-gated
 * via _dispatch_tray_call_tool) instead of a bespoke IPC route -- the
 * same mechanism main.html's GitHub panel already uses for
 * github_check_updates. run_gauntlet accepts exactly one of
 * code/claim/url/book+chapter as the idea source, alongside required
 * symbol+hypothesis and an optional provenance string.
 * @param {Object} fields - { symbol, hypothesis, source, code, claim,
 *   url, book, chapter, provenance }. `source` selects which of
 *   code/claim/url/book+chapter to include.
 */
function buildRunGauntletEvent(fields) {
  const args = { symbol: fields.symbol, hypothesis: fields.hypothesis };
  if (fields.provenance) args.provenance = fields.provenance;
  if (fields.source === 'claim') {
    args.claim = fields.claim;
  } else if (fields.source === 'url') {
    args.url = fields.url;
  } else if (fields.source === 'book') {
    args.book = fields.book;
    args.chapter = fields.chapter;
  } else {
    args.code = fields.code;
  }
  return { type: 'call_tool', data: { name: 'run_gauntlet', args: args } };
}

/**
 * Renders a StrategyCard into the given container element.
 * @param {Object} card - StrategyCard object from gauntlet
 * @param {HTMLElement} container - DOM element to render into
 */
function renderStrategyCard(card, container) {
  if (!container) return;

  const verdictClass = card.verdict === "VALIDATED" ? "pass" : card.verdict === "UNVALIDATED" ? "fail" : "warn";

  container.innerHTML = `
    <div class="strategy-card">
      <div class="card-header verdict-${verdictClass}">
        <h2>${card.verdict}</h2>
        <p class="hypothesis">${card.hypothesis}</p>
      </div>
      <div class="card-section">
        <h3>Provenance</h3>
        <p>${card.provenance}</p>
      </div>
      <div class="card-section">
        <h3>Key Metrics</h3>
        <ul>
          <li>Sharpe: ${card.sharpe.toFixed(3)} (95% CI: [${card.sharpe_ci[0].toFixed(3)}, ${card.sharpe_ci[1].toFixed(3)}])</li>
          <li>Total Return: ${(card.total_return * 100).toFixed(2)}% (95% CI: [${(card.total_return_ci[0] * 100).toFixed(2)}%, ${(card.total_return_ci[1] * 100).toFixed(2)}%])</li>
        </ul>
      </div>
      <div class="card-section">
        <h3>Gauntlet Gates</h3>
        <table class="gate-table">
          <thead><tr><th>Gate</th><th>Status</th><th>Value</th><th>Threshold</th><th>Details</th></tr></thead>
          <tbody>
            ${card.gates.map(g => `
              <tr class="${g.passed ? "pass" : "fail"}">
                <td>${g.name.replace(/_/g, " ").toUpperCase()}</td>
                <td>${g.passed ? "PASS" : "FAIL"}</td>
                <td>${g.metric.toFixed(4)}</td>
                <td>${g.threshold.toFixed(4)}</td>
                <td>${g.details}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
      <div class="card-section">
        <h3>Equity Curve</h3>
        <canvas id="equity-canvas" style="width:100%; height:150px;"></canvas>
      </div>
      <div class="card-section caveat">
        <p><strong>Caveat:</strong> ${card.survivorship_bias_caveat}</p>
      </div>
    </div>
  `;

  // Simple equity curve plot using canvas
  const canvas = container.querySelector("#equity-canvas");
  if (canvas && card.equity_curve.length > 0) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.clientWidth;
    const h = canvas.height = canvas.clientHeight;
    const min = Math.min(...card.equity_curve);
    const max = Math.max(...card.equity_curve);
    const range = max - min || 1;

    ctx.beginPath();
    card.equity_curve.forEach((val, i) => {
      const x = (i / (card.equity_curve.length - 1)) * w;
      const y = h - ((val - min) / range) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = card.verdict === "VALIDATED" ? "#2ecc71" : "#e74c3c";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // Inject minimal styles
  const styleId = "trading-panel-styles";
  if (!document.getElementById(styleId)) {
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      .strategy-card { font-family: sans-serif; padding: 16px; border: 1px solid #ddd; border-radius: 6px; background: #fff; }
      .card-header { border-bottom: 1px solid #eee; padding-bottom: 10px; margin-bottom: 12px; }
      .verdict-VALIDATED { border-left: 5px solid #2ecc71; }
      .verdict-UNVALIDATED { border-left: 5px solid #e74c3c; }
      .verdict-PROVISIONAL { border-left: 5px solid #f1c40f; }
      h2 { margin: 0 0 4px; }
      .hypothesis { color: #555; font-size: 0.9em; }
      .card-section { margin-bottom: 14px; }
      .gate-table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
      .gate-table th, .gate-table td { padding: 6px 8px; border: 1px solid #eee; text-align: left; }
      .gate-table tr.pass td { background: #f0fff4; }
      .gate-table tr.fail td { background: #fff5f5; }
      .caveat { background: #f8f9fa; padding: 8px; border-radius: 4px; font-size: 0.85em; color: #666; }
    `;
    document.head.appendChild(style);
  }
}

/**
 * Renders a live strategy card showing lifecycle status, fills, and alerts.
 * @param {Object} data - { name, status, live_trades, equity_curve, alerts, fills }
 * @param {HTMLElement} container
 */
function renderLiveStrategyCard(data, container) {
  if (!container) return;
  
  const statusClass = data.status === 'live' ? 'status-live' : data.status === 'paper' ? 'status-paper' : 'status-halted';
  
  container.innerHTML = `
    <div class="live-strategy-card">
      <div class="card-header ${statusClass}">
        <h2>${data.name}</h2>
        <span class="lifecycle-badge">${data.status.toUpperCase()}</span>
      </div>
      <div class="card-section">
        <h3>Performance</h3>
        <ul>
          <li>Live Trades: ${data.live_trades ?? 0}</li>
          <li>Distinct Trading Days: ${data.distinct_days ?? 0}</li>
          <li>Equity Curve: ${data.equity_curve ? '📈' : '⏳'}</li>
        </ul>
        ${(data.live_trades ?? 0) < 30 || (data.distinct_days ?? 0) < 30 ? 
          `<p style="color:var(--warning); font-size:0.85em; margin-top:6px;">
             Sample insufficient: ${((data.live_trades ?? 0) < 30 ? 'trades < 30' : '')} ${((data.live_trades ?? 0) < 30 && (data.distinct_days ?? 0) < 30 ? 'and ' : '')} ${((data.distinct_days ?? 0) < 30 ? 'distinct days < 30' : '')}
           </p>` : ''}
      </div>
      <div class="card-section">
        <h3>Recent Fills</h3>
        <table class="fills-table">
          <thead><tr><th>Symbol</th><th>Side</th><th>PnL</th><th>Phase</th></tr></thead>
          <tbody>
            ${(data.recent_fills || []).slice(0, 5).map(f => `
              <tr>
                <td>${f.symbol}</td>
                <td>${f.side.toUpperCase()}</td>
                <td>${f.pnl.toFixed(2)}</td>
                <td><span class="phase-badge ${f.phase === 'live' ? 'live' : 'paper'}">${(f.phase || 'paper').toUpperCase()}</span></td>
              </tr>
            `).join("") || '<tr><td colspan="4">No fills yet</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="card-section">
        <h3>Alert History</h3>
        <ul class="alert-list">
          ${(data.alerts || []).slice(0, 5).map(a => `
            <li class="alert-${a.severity}">[${a.severity.toUpperCase()}] ${a.event_type}: ${a.message}</li>
          `).join("") || '<li>No alerts</li>'}
        </ul>
      </div>
    </div>
  `;

  const styleId = "live-trading-panel-styles";
  if (!document.getElementById(styleId)) {
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      .live-strategy-card { font-family: sans-serif; padding: 16px; border: 1px solid #ddd; border-radius: 6px; background: #fff; margin-top: 20px; }
      .card-header { border-bottom: 1px solid #eee; padding-bottom: 10px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
      .status-live { border-left: 5px solid #3498db; }
      .status-paper { border-left: 5px solid #f1c40f; }
      .status-halted { border-left: 5px solid #e74c3c; }
      .lifecycle-badge { font-size: 0.85em; padding: 4px 10px; border-radius: 6px; font-weight: bold; text-transform: uppercase; }
      .status-live .lifecycle-badge { background: #3498db; color: #fff; }
      .status-paper .lifecycle-badge { background: #f1c40f; color: #000; }
      .status-halted .lifecycle-badge { background: #e74c3c; color: #fff; }
      .phase-badge { padding: 2px 6px; border-radius: 4px; font-size: 0.75em; text-transform: uppercase; font-weight: 500; }
      .phase-badge.live { background: #3498db; color: #fff; }
      .phase-badge.paper { background: #f1c40f; color: #000; }
      .fills-table { width: 100%; border-collapse: collapse; font-size: 0.85em; margin-top: 6px; }
      .fills-table th, .fills-table td { padding: 4px 6px; border: 1px solid #eee; text-align: left; }
      .alert-list { list-style: none; padding: 0; font-size: 0.85em; }
      .alert-list li { padding: 4px 0; border-bottom: 1px solid #f0f0f0; }
      .alert-critical { color: #e74c3c; }
      .alert-warning { color: #f39c12; }
      .alert-info { color: #3498db; }
    `;
    document.head.appendChild(style);
  }
}

/* ── S29 (#892), decisions #48-#51 -- Trading pane "Tickers" sub-tab ──── */

/**
 * Prepares the Tickers mount with a loading placeholder. Mirrors
 * initTradingPanel's contract -- the caller (main.html) sends the actual
 * `trading_tickers_poll` request itself.
 */
function initTickersView() {
  const mount = document.getElementById('trading-tickers-mount');
  if (!mount) return;
  mount.innerHTML = '<div style="padding:16px; color:var(--text-muted); text-align:center;">Loading tickers…</div>';
}

function _tickerStageLabel(stage) {
  if (stage === 'screened') return 'Screened';
  if (stage === 'rejected') return 'Rejected';
  if (stage === 'validated') return 'Validated';
  return 'Charting';
}

/**
 * Renders one ticker's card markup. Four stages (decision #49, S30/#894) --
 * a screened ticker with no strategy yet gets a one-line status, a rejected
 * ticker shows its most recent gauntlet attempt's own reason, a validated
 * strategy with zero fills gets a one-line status, and only a strategy with
 * at least one fill gets a canvas (one per phase segment -- decision #50
 * keeps paper/live as separate, never-joined charts).
 */
function _renderTickerCard(ticker, ti) {
  const strategies = ticker.strategies || [];
  let body;
  if (ticker.stage === 'rejected' && strategies.length === 0) {
    const reason = ticker.reason || 'no reason recorded';
    body = `<p class="trd-ticker-status trd-ticker-rejected">Rejected by the gauntlet — ${reason}</p>`;
  } else if (ticker.stage === 'screened' || strategies.length === 0) {
    body = '<p class="trd-ticker-status">Screened — no strategy yet.</p>';
  } else {
    body = strategies.map((s, si) => {
      const segments = s.segments || [];
      if (segments.length === 0) {
        return `
          <div class="trd-ticker-strategy">
            <div class="trd-ticker-strategy-name">${s.name} <span class="status-badge">${s.status.toUpperCase()}</span></div>
            <p class="trd-ticker-status">Validated — awaiting first paper trade.</p>
          </div>`;
      }
      return `
        <div class="trd-ticker-strategy">
          <div class="trd-ticker-strategy-name">${s.name} <span class="status-badge">${s.status.toUpperCase()}</span></div>
          ${segments.map((seg, gi) => `
            <div class="trd-ticker-segment">
              <div class="trd-ticker-segment-label">${seg.phase.toUpperCase()} vs. buy-and-hold</div>
              <canvas id="trd-ticker-canvas-${ti}-${si}-${gi}" class="trd-ticker-canvas" data-phase="${seg.phase}"></canvas>
            </div>
          `).join('')}
        </div>`;
    }).join('');
  }
  return `
    <div class="trd-ticker-card">
      <div class="trd-ticker-header">
        <h3>${ticker.symbol}</h3>
        <span class="trd-ticker-stage-badge trd-ticker-stage-${ticker.stage}">${_tickerStageLabel(ticker.stage)}</span>
      </div>
      ${body}
    </div>`;
}

/**
 * Draws one phase segment's chart: the strategy's own cumulative-PnL line
 * (with a dot per trade) plus the buy-and-hold benchmark line, both on a
 * shared time-based x-axis and shared $ y-axis so they're directly
 * comparable (decision #49). Stores each dot's screen position on the
 * canvas element itself so _wireTickerChartHover can hit-test it -- no
 * separate index kept alongside the DOM.
 */
function _drawTickerChart(canvas, segment) {
  if (!canvas) return;
  const points = segment.points || [];
  if (points.length === 0) return;
  const benchmark = segment.benchmark || [];
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.clientWidth;
  const h = canvas.height = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);

  const toTime = (ts) => new Date(ts).getTime();
  const times = points.map((p) => toTime(p.ts)).concat(benchmark.map((b) => toTime(b.ts)));
  const values = points.map((p) => p.equity).concat(benchmark.map((b) => b.value)).concat([0]);
  const tMin = Math.min(...times), tMax = Math.max(...times);
  const vMin = Math.min(...values), vMax = Math.max(...values);
  const tRange = (tMax - tMin) || 1;
  const vRange = (vMax - vMin) || 1;
  const xFor = (t) => ((t - tMin) / tRange) * w;
  const yFor = (v) => h - ((v - vMin) / vRange) * h;
  const lineColor = segment.phase === 'live' ? '#3498db' : '#f1c40f';

  if (benchmark.length > 1) {
    ctx.beginPath();
    benchmark.forEach((b, i) => {
      const x = xFor(toTime(b.ts)), y = yFor(b.value);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = '#95a5a6';
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  const dots = [];
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = xFor(toTime(p.ts)), y = yFor(p.equity);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    dots.push({ x: x, y: y, point: p });
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.fillStyle = lineColor;
  dots.forEach((d) => {
    ctx.beginPath();
    ctx.arc(d.x, d.y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  });

  canvas._tickerDots = dots;
}

function _tickerTooltipEl() {
  let el = document.getElementById('trd-ticker-tooltip');
  if (!el) {
    el = document.createElement('div');
    el.id = 'trd-ticker-tooltip';
    el.className = 'trd-ticker-tooltip';
    el.hidden = true;
    document.body.appendChild(el);
  }
  return el;
}

/**
 * Hovering a trade dot shows which strategy made that trade and the
 * trade's own details (acceptance criterion). One shared tooltip element
 * reused across every chart on the page rather than one per canvas.
 */
function _wireTickerChartHover(canvas) {
  const tooltip = _tickerTooltipEl();
  canvas.addEventListener('mousemove', (e) => {
    const dots = canvas._tickerDots || [];
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let nearest = null, bestDist = 8;
    dots.forEach((d) => {
      const dist = Math.hypot(d.x - mx, d.y - my);
      if (dist < bestDist) { bestDist = dist; nearest = d; }
    });
    if (nearest) {
      const p = nearest.point;
      tooltip.innerHTML =
        '<strong>' + p.strategy + '</strong><br>' +
        p.side.toUpperCase() + ' @ $' + p.price.toFixed(2) + '<br>' +
        'PnL: $' + p.pnl.toFixed(2) + '<br>' +
        new Date(p.ts).toLocaleString();
      tooltip.style.left = (e.clientX + 12) + 'px';
      tooltip.style.top = (e.clientY + 12) + 'px';
      tooltip.hidden = false;
    } else {
      tooltip.hidden = true;
    }
  });
  canvas.addEventListener('mouseleave', () => { tooltip.hidden = true; });
}

function _injectTickerStyles() {
  const styleId = 'trading-tickers-styles';
  if (document.getElementById(styleId)) return;
  const style = document.createElement('style');
  style.id = styleId;
  style.textContent = `
    .trd-tickers-list { display: flex; flex-direction: column; gap: 12px; padding: 12px; }
    .trd-ticker-card { border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: var(--bg-elev); }
    .trd-ticker-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
    .trd-ticker-header h3 { margin: 0; }
    .trd-ticker-stage-badge { font-size: 0.75em; padding: 3px 8px; border-radius: 10px; font-weight: 600; text-transform: uppercase; background: var(--bg); color: var(--text-muted); }
    .trd-ticker-stage-charting { background: #2ecc71; color: #fff; }
    .trd-ticker-stage-rejected { background: #e74c3c; color: #fff; }
    .trd-ticker-status { color: var(--text-muted); font-size: 0.9em; margin: 4px 0; }
    .trd-ticker-status.trd-ticker-rejected { color: #e74c3c; }
    .trd-ticker-strategy { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border); }
    .trd-ticker-strategy-name { font-weight: 500; margin-bottom: 4px; }
    .trd-ticker-segment { margin-top: 6px; }
    .trd-ticker-segment-label { font-size: 0.75em; color: var(--text-muted); margin-bottom: 2px; }
    .trd-ticker-canvas { width: 100%; height: 120px; }
    .trd-ticker-tooltip { position: fixed; z-index: 10000; background: var(--bg-elev, #222); color: var(--text, #fff); border: 1px solid var(--border, #444); border-radius: 4px; padding: 6px 10px; font-size: 0.8em; pointer-events: none; }
  `;
  document.head.appendChild(style);
}

/**
 * Renders a `trading_tickers_update` broadcast into the mount.
 * @param {Object} data - { tickers: [...] } from cerebral/main.py's
 *   _trading_tickers_data() / cerebral.trading.ticker_view.build_ticker_view.
 * @param {HTMLElement} [container] - defaults to #trading-tickers-mount
 */
function renderTickersUpdate(data, container) {
  const mount = container || document.getElementById('trading-tickers-mount');
  if (!mount) return;
  const tickers = (data && data.tickers) || [];
  if (tickers.length === 0) {
    mount.innerHTML = '<div style="padding:16px; color:var(--text-muted); text-align:center;">No tickers in play yet — nothing screened, and no strategy created.</div>';
    return;
  }

  mount.innerHTML = '<div class="trd-tickers-list">' +
    tickers.map((t, ti) => _renderTickerCard(t, ti)).join('') + '</div>';

  // Canvas drawing needs a real DOM (querySelector + canvas 2D context) --
  // guarded so this stays a no-op against the minimal fake `document`/
  // container this file's own tests use (see trading-panel.test.js's
  // header comment: no jsdom in this repo, only innerHTML is exercised).
  if (typeof mount.querySelectorAll === 'function') {
    tickers.forEach((t, ti) => {
      (t.strategies || []).forEach((s, si) => {
        (s.segments || []).forEach((seg, gi) => {
          const canvas = mount.querySelector('#trd-ticker-canvas-' + ti + '-' + si + '-' + gi);
          if (canvas) {
            _drawTickerChart(canvas, seg);
            _wireTickerChartHover(canvas);
          }
        });
      });
    });
  }

  _injectTickerStyles();
}

return {
  initTradingPanel:    initTradingPanel,
  renderTradingUpdate: renderTradingUpdate,
  renderBooksPanel: renderBooksPanel,
  renderStrategyCard:  renderStrategyCard,
  renderLiveStrategyCard: renderLiveStrategyCard,
  buildStrategyEditEvent: buildStrategyEditEvent,
  buildRunGauntletEvent: buildRunGauntletEvent,
  initTickersView:     initTickersView,
  renderTickersUpdate: renderTickersUpdate,
  buildStartDiscoveryEvent: buildStartDiscoveryEvent,
  buildStopDiscoveryEvent: buildStopDiscoveryEvent,
  buildUploadBookEvent: buildUploadBookEvent,
  buildStopBookEvent: buildStopBookEvent,
  buildRetryBookEvent: buildRetryBookEvent,
  buildResumeBookEvent: buildResumeBookEvent,
  buildDeleteBookEvent: buildDeleteBookEvent,
  buildHaltStrategyEvent: buildHaltStrategyEvent,
  buildResumeStrategyEvent: buildResumeStrategyEvent,
};

}));
