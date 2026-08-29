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
 * Paper-trading control block (S34/#901) -- Start/Stop the autonomous
 * paper-trade dispatch loop + a starting-capital input, mirroring the
 * Discovery control above exactly. Rendered alongside it, above the
 * strategy list/empty-state.
 * @param {Object} [paperControl] - { enabled, starting_capital } from
 *   cerebral/main.py's _trading_broadcast(); undefined/null renders as
 *   running with the $10,000 default (matches the setting's own default).
 */
function _renderPaperControl(paperControl) {
  const running = !paperControl || paperControl.enabled !== false;
  const capital = (paperControl && paperControl.starting_capital != null)
    ? paperControl.starting_capital : 10000;
  return `
    <div class="paper-control">
      <h3>Paper Trading</h3>
      <div class="paper-control-row">
        <button class="paper-start-btn" ${running ? 'disabled' : ''}>Start</button>
        <button class="paper-stop-btn" ${running ? '' : 'disabled'}>Stop</button>
        <span class="paper-status">${running ? 'Running' : 'Stopped'}</span>
      </div>
      <div class="paper-control-row">
        <label for="paper-capital-input">Starting capital ($, simulated)</label>
        <input type="number" class="paper-capital-input" min="0" step="100" value="${capital}">
        <button class="paper-capital-save-btn">Save</button>
        <span class="paper-capital-hint">takes effect after next restart</span>
      </div>
    </div>
  `;
}

/**
 * Wires the paper-trading control's Start/Stop buttons (call_tool, same
 * pattern as _wireDiscoveryControl) and the capital input's Save button
 * (set_setting -- a plain numeric setting, not voice/chat-reachable the
 * way start_trading/stop_trading are, so no dedicated tool for it).
 */
function _wirePaperControl(mount, sendEventFn) {
  const startBtn = mount.querySelector('.paper-start-btn');
  const stopBtn = mount.querySelector('.paper-stop-btn');
  const capitalInput = mount.querySelector('.paper-capital-input');
  const capitalSaveBtn = mount.querySelector('.paper-capital-save-btn');
  if (startBtn) {
    startBtn.addEventListener('click', () => {
      if (!sendEventFn) return;
      sendEventFn({ type: 'call_tool', data: { name: 'start_trading', args: {} } });
      sendEventFn({ type: 'trading_poll' });
    });
  }
  if (stopBtn) {
    stopBtn.addEventListener('click', () => {
      if (!sendEventFn) return;
      sendEventFn({ type: 'call_tool', data: { name: 'stop_trading', args: {} } });
      sendEventFn({ type: 'trading_poll' });
    });
  }
  if (capitalSaveBtn && capitalInput) {
    capitalSaveBtn.addEventListener('click', () => {
      if (!sendEventFn) return;
      const value = parseFloat(capitalInput.value);
      if (isNaN(value) || value < 0) return;
      sendEventFn({ type: 'set_setting', data: { key: 'trading_paper_starting_capital', value: value } });
    });
  }
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

  // S31 (#896)/S34 (#901): rendered (and wired) in both branches below --
  // both controls are independent of whether any strategy exists yet, so
  // they must not disappear behind the empty-state message. Styles
  // injected here, unconditionally, so both branches have them (previously
  // only the non-empty branch injected any styles at all).
  const discoveryHtml = _renderDiscoveryControl(data && data.discovery);
  const paperControlHtml = _renderPaperControl(data && data.paper_control);
  _injectTradingPanelStyles();

  if (!data || !data.positions || data.positions.length === 0) {
    mount.innerHTML = paperControlHtml + discoveryHtml + '<div style="padding:16px; color:var(--text-muted); text-align:center;">No active strategies. Create one via the Scheduler or Strategy Gauntlet.</div>';
    _wireDiscoveryControl(mount, sendEventFn);
    _wirePaperControl(mount, sendEventFn);
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

  mount.innerHTML = paperControlHtml + discoveryHtml + `
    <div class="trading-panel-layout">
      <div class="strategy-list">
        <h3>Strategies</h3>
        <ul>
          ${state.strategies.map((s, i) => {
            const trades = s.live_trades || 0;
            // S46b: confidence_weight/is_expansion added to the broadcast by
            // S46a -- optional-chained since an older cached broadcast (or a
            // strategy predating S38) may not carry them yet.
            const cw = typeof s.confidence_weight === 'number' ? s.confidence_weight : 0;
            const cwClass = cw > 0 ? 'positive' : cw < 0 ? 'negative' : 'neutral';
            const expansionBadge = s.is_expansion ? '<span class="expansion-badge">expanded</span> ' : '';
            return `
            <li class="${i === state.selectedIdx ? 'selected' : ''}" data-idx="${i}">
              ${expansionBadge}${s.name} <span class="status-badge">${s.status.toUpperCase()}</span> <span class="trade-count-badge">${trades} trade${trades === 1 ? '' : 's'}</span> <span class="confidence-badge ${cwClass}">${cw.toFixed(2)}</span>
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
  _wirePaperControl(mount, sendEventFn);

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
    .confidence-badge { font-size: 0.7em; padding: 2px 5px; border-radius: 3px; margin-left: 6px; font-weight: bold; }
    .confidence-badge.positive { background: #e8f5e9; color: #2e7d32; }
    .confidence-badge.negative { background: #ffebee; color: #c62828; }
    .confidence-badge.neutral { background: #eee; color: #666; }
    .expansion-badge { font-size: 0.7em; padding: 2px 5px; border-radius: 3px; background: #ede7f6; color: #5e35b1; }
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
    .paper-control { margin-bottom: 16px; padding: 12px; background: var(--bg-elev, #f8f9fa); border-radius: 6px; border: 1px solid var(--border, #eee); font-family: sans-serif; }
    .paper-control h3 { margin: 0 0 8px; font-size: 14px; color: var(--text, #333); }
    .paper-control-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
    .paper-control-row:last-child { margin-bottom: 0; }
    .paper-control label { color: var(--text-muted, #555); font-size: 0.9em; }
    .paper-start-btn, .paper-stop-btn { padding: 4px 10px; border: none; border-radius: 4px; cursor: pointer; font-weight: 500; color: #fff; }
    .paper-start-btn { background: #2ecc71; }
    .paper-stop-btn { background: #e74c3c; }
    .paper-start-btn:disabled, .paper-stop-btn:disabled { background: var(--border, #ccc); color: var(--text-muted, #888); cursor: default; }
    .paper-status { font-size: 12px; color: var(--text-muted, #555); }
    .paper-capital-input { width: 100px; padding: 3px 6px; border: 1px solid var(--border, #ccc); border-radius: 3px; background: var(--bg, #fff); color: var(--text, #333); }
    .paper-capital-save-btn { padding: 3px 10px; border: none; border-radius: 4px; cursor: pointer; font-weight: 500; color: #fff; background: #3498db; }
    .paper-capital-hint { font-size: 11px; color: var(--text-muted, #888); font-style: italic; }
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

/* ── S35 (#911/#912) -- Overview sub-tab: one multi-line graph across every
 * strategy at once, each line hoverable for that strategy's current status
 * + total gain/loss, plus a grand total near the graph. Same canvas +
 * shared-tooltip pattern as the Tickers chart above (_drawTickerChart/
 * _wireTickerChartHover) -- flattened per-point hit-testing, not per-line
 * interpolation, since equity_curve is index-based (no real timestamps),
 * same as renderStrategyCard's own single-line canvas. ────────────────── */

// Cycled by strategy index -- fixed palette, no charting library.
const _OVERVIEW_LINE_COLORS = [
  '#3498db', '#e74c3c', '#2ecc71', '#f1c40f', '#9b59b6',
  '#1abc9c', '#e67e22', '#34495e', '#ff6b9d', '#00bcd4',
];

/**
 * Draws every strategy's equity curve on one shared canvas, one colored
 * line each, all sharing one x (curve index) / y ($) scale so they're
 * directly comparable. Stores each point's screen position + owning
 * strategy on the canvas element for _wireOverviewChartHover to hit-test.
 * @param {HTMLCanvasElement} canvas
 * @param {Array} strategies - data.positions (name/status/equity_curve)
 */
/**
 * Lays out every line's points in pixel space (with room reserved for
 * axis labels) and does the static paint. Stores the layout on the
 * canvas element (_overviewLines/_overviewScale) so _paintOverviewChart
 * can repaint on every hover frame without recomputing scales, and so
 * _wireOverviewChartHover can interpolate a line's y at any x, not just
 * hit-test its raw vertices (raw-vertex hit-testing is what caused the
 * "flashes instead of sticking to the line" bug -- with only a handful
 * of trades, vertices can be tens of pixels apart, leaving dead gaps
 * where nothing was ever within the old fixed hit radius).
 */
function _drawOverviewChart(canvas, strategies) {
  if (!canvas) return;
  const lines = (strategies || []).filter((s) => s.equity_curve && s.equity_curve.length > 0);
  const w = canvas.width = canvas.clientWidth;
  const h = canvas.height = canvas.clientHeight;
  canvas._overviewLines = [];
  canvas._overviewScale = null;
  if (lines.length === 0) { _paintOverviewChart(canvas); return; }

  const maxLen = Math.max(...lines.map((s) => s.equity_curve.length));
  const allValues = lines.reduce((acc, s) => acc.concat(s.equity_curve), [0]);
  const vMin = Math.min(...allValues), vMax = Math.max(...allValues);
  const vRange = (vMax - vMin) || 1;
  // Left padding for $ axis labels, bottom padding for the x-axis label.
  const padL = 44, padR = 6, padT = 6, padB = 16;
  const plotW = Math.max(w - padL - padR, 1);
  const plotH = Math.max(h - padT - padB, 1);
  const xFor = (i) => padL + (maxLen > 1 ? (i / (maxLen - 1)) * plotW : plotW / 2);
  const yFor = (v) => padT + plotH - ((v - vMin) / vRange) * plotH;

  canvas._overviewScale = { vMin: vMin, vMax: vMax, plotW: plotW, plotH: plotH, padL: padL, padT: padT };
  canvas._overviewLines = lines.map((s, li) => ({
    name: s.name,
    status: s.status,
    color: _OVERVIEW_LINE_COLORS[li % _OVERVIEW_LINE_COLORS.length],
    totalGainLoss: s.equity_curve[s.equity_curve.length - 1],
    points: s.equity_curve.map((v, i) => ({ x: xFor(i), y: yFor(v) })),
  }));

  _paintOverviewChart(canvas);
}

/**
 * The actual paint step -- clears and redraws from the layout
 * _drawOverviewChart already computed, optionally with a hover highlight
 * (a dot + a dashed vertical guide connecting it back down to the x-axis,
 * the acceptance criterion: "a small dot that connects to show focus on
 * where the line is"). Cheap enough to call on every mousemove -- these
 * are short trade-count-bounded curves, not dense time-series data.
 * @param {HTMLCanvasElement} canvas
 * @param {Object} [highlight] - { x, y, color } in canvas pixel space
 */
function _paintOverviewChart(canvas, highlight) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const lines = canvas._overviewLines || [];
  const scale = canvas._overviewScale;
  if (lines.length === 0 || !scale) return;

  // $ axis gridlines/labels -- the acceptance criterion: "no information
  // on the graph itself to show what that represents."
  const gridVals = [scale.vMax, (scale.vMax + scale.vMin) / 2, scale.vMin];
  ctx.font = '10px sans-serif';
  ctx.textBaseline = 'middle';
  gridVals.forEach((v) => {
    const y = scale.padT + scale.plotH - ((v - scale.vMin) / ((scale.vMax - scale.vMin) || 1)) * scale.plotH;
    ctx.beginPath();
    ctx.moveTo(scale.padL, y);
    ctx.lineTo(scale.padL + scale.plotW, y);
    ctx.strokeStyle = 'rgba(128,128,128,0.15)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = 'rgba(128,128,128,0.8)';
    ctx.textAlign = 'right';
    ctx.fillText('$' + v.toFixed(0), scale.padL - 6, y);
  });
  // Zero line, distinct from the plain gridlines -- "above/below break-even" at a glance.
  if (scale.vMin < 0 && scale.vMax > 0) {
    const y0 = scale.padT + scale.plotH - ((0 - scale.vMin) / ((scale.vMax - scale.vMin) || 1)) * scale.plotH;
    ctx.beginPath();
    ctx.moveTo(scale.padL, y0);
    ctx.lineTo(scale.padL + scale.plotW, y0);
    ctx.strokeStyle = 'rgba(128,128,128,0.4)';
    ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  lines.forEach((line) => {
    ctx.beginPath();
    line.points.forEach((p, i) => { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
    ctx.strokeStyle = line.color;
    ctx.lineWidth = 2;
    ctx.stroke();
  });

  ctx.fillStyle = 'rgba(128,128,128,0.8)';
  ctx.textAlign = 'center';
  ctx.fillText('Trade #', scale.padL + scale.plotW / 2, h - 4);

  if (highlight) {
    ctx.beginPath();
    ctx.moveTo(highlight.x, scale.padT);
    ctx.lineTo(highlight.x, scale.padT + scale.plotH);
    ctx.strokeStyle = 'rgba(128,128,128,0.5)';
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath();
    ctx.arc(highlight.x, highlight.y, 5, 0, Math.PI * 2);
    ctx.fillStyle = highlight.color;
    ctx.fill();
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

function _overviewTooltipEl() {
  let el = document.getElementById('trd-overview-tooltip');
  if (!el) {
    el = document.createElement('div');
    el.id = 'trd-overview-tooltip';
    el.className = 'trd-ticker-tooltip'; // reuses the Tickers tooltip's own styling
    el.hidden = true;
    document.body.appendChild(el);
  }
  return el;
}

/**
 * Hovering anywhere along a line (not just near a vertex) shows that
 * strategy's name, current status, and total gain/loss, plus repaints a
 * highlight dot on the line under the cursor. Interpolates each line's y
 * at the cursor's x (clamped to that line's own x-range) rather than
 * hit-testing raw vertices -- continuous coverage across the whole line,
 * not just near its trade points, which is what the old fixed-radius
 * per-vertex test left as dead gaps ("flashes instead of sticking").
 */
function _wireOverviewChartHover(canvas) {
  const tooltip = _overviewTooltipEl();
  canvas.addEventListener('mousemove', (e) => {
    const lines = canvas._overviewLines || [];
    const scale = canvas._overviewScale;
    if (lines.length === 0 || !scale) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;

    let nearestLine = null, nearestY = null, bestDist = 40; // px, vertical tolerance
    lines.forEach((line) => {
      const pts = line.points;
      if (pts.length === 0) return;
      let y;
      if (mx <= pts[0].x) {
        y = pts[0].y;
      } else if (mx >= pts[pts.length - 1].x) {
        y = pts[pts.length - 1].y;
      } else {
        y = pts[pts.length - 1].y;
        for (let i = 0; i < pts.length - 1; i++) {
          if (mx >= pts[i].x && mx <= pts[i + 1].x) {
            const dx = pts[i + 1].x - pts[i].x;
            const t = dx === 0 ? 0 : (mx - pts[i].x) / dx;
            y = pts[i].y + t * (pts[i + 1].y - pts[i].y);
            break;
          }
        }
      }
      const dist = Math.abs(my - y);
      if (dist < bestDist) { bestDist = dist; nearestLine = line; nearestY = y; }
    });

    // Skip the repaint (a full clear + redraw of every line + gridlines)
    // when the highlighted point hasn't meaningfully changed since the
    // last mousemove -- raw mousemove can fire far more often than the
    // cursor visibly moves, and a synchronous full repaint on every one
    // of those is real jank on a big chart (61 strategies' worth of
    // lines), which reads as its own kind of "glitchy."
    const frameKey = nearestLine ? (nearestLine.name + '|' + Math.round(mx)) : null;
    if (frameKey === canvas._overviewLastFrameKey) return;
    canvas._overviewLastFrameKey = frameKey;

    if (nearestLine) {
      const gl = nearestLine.totalGainLoss;
      const glColor = gl >= 0 ? '#2ecc71' : '#e74c3c';
      tooltip.innerHTML =
        '<strong>' + nearestLine.name + '</strong><br>' +
        'Status: ' + nearestLine.status + '<br>' +
        'Total gain/loss: <span style="color:' + glColor + '">$' + gl.toFixed(2) + '</span>';
      tooltip.style.left = (e.clientX + 12) + 'px';
      tooltip.style.top = (e.clientY + 12) + 'px';
      tooltip.hidden = false;
      _paintOverviewChart(canvas, { x: mx, y: nearestY, color: nearestLine.color });
    } else {
      tooltip.hidden = true;
      _paintOverviewChart(canvas);
    }
  });
  canvas.addEventListener('mouseleave', () => {
    canvas._overviewLastFrameKey = null;
    tooltip.hidden = true;
    _paintOverviewChart(canvas);
  });
}

function _injectOverviewStyles() {
  const styleId = 'trading-overview-styles';
  if (document.getElementById(styleId)) return;
  const style = document.createElement('style');
  style.id = styleId;
  style.textContent = `
    .trd-overview { padding: 12px; }
    .trd-overview-total { font-size: 1.4em; font-weight: 600; margin-bottom: 12px; }
    .trd-overview-total .trd-overview-total-value.positive { color: #2ecc71; }
    .trd-overview-total .trd-overview-total-value.negative { color: #e74c3c; }
    .trd-overview-canvas { width: 100%; height: 320px; }
    .trd-overview-legend { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; font-size: 0.85em; }
    .trd-overview-legend-item { display: flex; align-items: center; gap: 4px; color: var(--text-muted); }
    .trd-overview-legend-swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
    .trd-overview-section-title { margin: 20px 0 8px; font-size: 13px; color: var(--text-muted, #666); text-transform: uppercase; letter-spacing: 0.05em; }
    .trd-overview-placeholder { padding: 16px; color: var(--text-muted); text-align: center; font-size: 0.9em; }
    .trd-overview-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .trd-overview-reset-btn { padding: 4px 10px; font-size: 0.85em; }
    .trd-archive-row { border: 1px solid var(--border-color, #333); border-radius: 4px; margin-bottom: 6px; overflow: hidden; }
    .trd-archive-summary { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; cursor: pointer; font-size: 0.9em; }
    .trd-archive-summary:hover { background: var(--hover-bg, rgba(255,255,255,0.05)); }
    .trd-archive-pnl.positive { color: #2ecc71; }
    .trd-archive-pnl.negative { color: #e74c3c; }
    .trd-archive-fills { padding: 8px 10px; border-top: 1px solid var(--border-color, #333); font-size: 0.85em; max-height: 240px; overflow-y: auto; }
    .trd-archive-fills table { width: 100%; border-collapse: collapse; }
    .trd-archive-fills th, .trd-archive-fills td { text-align: left; padding: 3px 6px; }
    .trd-archive-fills th { color: var(--text-muted); font-weight: 500; }
  `;
  document.head.appendChild(style);
}

/* ── S37 (#920/#922-925) -- Reset paper trading + collapsible archive
 * history on the Overview tab. Expand-to-fetch state lives at module
 * scope (not per-mount) since there's only ever one Overview panel --
 * survives renderOverviewPanel's own DOM rebuilds the same way
 * _archiveExpanded's caller (the click handler) expects it to. ────────── */
var _archiveExpanded = {};   // {archive_id: true} for rows currently open
var _archiveFillsCache = {}; // {archive_id: fills[] | 'loading'}
// get_paper_archive_fills' tool_result broadcast doesn't echo back which
// archive_id it was for (cerebral/main.py's _dispatch_tray_call_tool only
// broadcasts {name, content, is_error}) -- track request order here
// instead, same single-flight-per-tool assumption every other tool_result
// consumer in this file already makes (e.g. run_gauntlet's one status
// element). Fine as long as requests resolve in the order sent, which a
// single WS connection processing call_tool sequentially guarantees.
var _archivePendingQueue = [];

/**
 * Called by main.html when a get_paper_archive_fills tool_result arrives.
 * Caches the fills against the oldest still-pending request; caller is
 * responsible for re-rendering the panel.
 */
function receiveArchiveFills(fills) {
  const archiveId = _archivePendingQueue.shift();
  if (archiveId === undefined) return;
  _archiveFillsCache[archiveId] = fills || [];
}

function _renderArchiveFillsTable(fills) {
  if (fills === 'loading') return '<div class="trd-overview-placeholder">Loading…</div>';
  if (!fills || fills.length === 0) return '<div class="trd-overview-placeholder">No fills in this block.</div>';
  const rows = fills.map((f) => `
    <tr>
      <td>${f.symbol || ''}</td>
      <td>${f.side || ''}</td>
      <td>${typeof f.qty === 'number' ? f.qty : ''}</td>
      <td>${typeof f.pnl === 'number' ? '$' + f.pnl.toFixed(2) : ''}</td>
      <td>${f.filled_at || f.timestamp || ''}</td>
    </tr>
  `).join('');
  return `<table>
    <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>P&amp;L</th><th>When</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

/**
 * Collapsible history section built from data.paper_archives (S37d), one
 * block per past reset_paper_trading call. Expanding a row that hasn't
 * been fetched yet triggers get_paper_archive_fills via sendEventFn;
 * receiveArchiveFills() + a re-render (main.html's tool_result handler)
 * fills it in once the response arrives.
 * @param {Array} archives - [{id, reset_at, total_pnl, trade_count,
 *   date_range_start, date_range_end}], newest first
 */
function _renderArchiveHistory(archives) {
  if (!archives || archives.length === 0) {
    return '<div class="trd-overview-placeholder">No past resets yet.</div>';
  }
  return archives.map((a) => {
    const isOpen = !!_archiveExpanded[a.id];
    const pnl = typeof a.total_pnl === 'number' ? a.total_pnl : 0;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';
    const range = (a.date_range_start || '?') + ' → ' + (a.date_range_end || '?');
    return `<div class="trd-archive-row" data-archive-id="${a.id}">
      <div class="trd-archive-summary" data-archive-toggle="${a.id}">
        <span>${a.reset_at || ''} · ${a.trade_count || 0} trades · ${range}</span>
        <span class="trd-archive-pnl ${pnlClass}">$${pnl.toFixed(2)}</span>
      </div>
      ${isOpen ? `<div class="trd-archive-fills">${_renderArchiveFillsTable(_archiveFillsCache[a.id])}</div>` : ''}
    </div>`;
  }).join('');
}

/**
 * Wires the Overview tab's Reset button (native confirm() -- irreversible
 * from the live-view's perspective even though the backend archives
 * rather than deletes, see reset_paper_trading's tool description) and
 * archive-row expand/collapse (delegated, since rows are rebuilt on
 * every structure-changing render).
 */
function _wireOverviewControls(mount, sendEventFn) {
  const resetBtn = mount.querySelector('.trd-overview-reset-btn');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (!sendEventFn) return;
      if (!window.confirm('Reset paper trading? Current fills/P&L will be archived into history and the simulated account restarts from its configured capital.')) return;
      sendEventFn({ type: 'call_tool', data: { name: 'reset_paper_trading', args: {} } });
    });
  }
  const historyMount = mount.querySelector('.trd-overview-history');
  if (historyMount) {
    historyMount.addEventListener('click', (ev) => {
      const toggle = ev.target.closest('[data-archive-toggle]');
      if (!toggle) return;
      const archiveId = toggle.getAttribute('data-archive-toggle');
      if (_archiveExpanded[archiveId]) {
        delete _archiveExpanded[archiveId];
      } else {
        _archiveExpanded[archiveId] = true;
        if (_archiveFillsCache[archiveId] === undefined && sendEventFn) {
          _archiveFillsCache[archiveId] = 'loading';
          _archivePendingQueue.push(archiveId);
          sendEventFn({ type: 'call_tool', data: { name: 'get_paper_archive_fills', args: { archive_id: parseInt(archiveId, 10) } } });
        }
      }
      // Structure changed (expanded set) -- force the caller to rebuild.
      mount._overviewStructureKey = null;
      _renderOverviewPanelCached(mount);
    });
  }
}

// Set by renderOverviewPanel on every call so the delegated click handler
// above can re-render without needing its own copy of the last payload.
var _lastOverviewRenderArgs = null;
function _renderOverviewPanelCached(mount) {
  if (!_lastOverviewRenderArgs) return;
  renderOverviewPanel(_lastOverviewRenderArgs.data, mount, _lastOverviewRenderArgs.sendEventFn);
}

/**
 * Groups a flat, chronological fill list (S35c/d's `data.all_fills`,
 * oldest first) into per-symbol running-P&L series -- reshaped into the
 * exact same {name, status, equity_curve} shape _drawOverviewChart/
 * _wireOverviewChartHover already expect, so the by-stock chart reuses
 * both functions unchanged rather than duplicating them. `status` is
 * repurposed here as a trade count (symbols don't have a lifecycle
 * status the way strategies do) -- still shown in the same tooltip slot.
 * @param {Array} allFills - [{symbol, pnl, ...}], oldest first
 */
function _groupFillsBySymbol(allFills) {
  const bySymbol = {};
  (allFills || []).forEach((f) => {
    if (!bySymbol[f.symbol]) bySymbol[f.symbol] = { running: 0, curve: [] };
    const entry = bySymbol[f.symbol];
    entry.running += (f.pnl || 0);
    entry.curve.push(entry.running);
  });
  return Object.keys(bySymbol).sort().map((symbol) => ({
    name: symbol,
    status: bySymbol[symbol].curve.length + (bySymbol[symbol].curve.length === 1 ? ' trade' : ' trades'),
    equity_curve: bySymbol[symbol].curve,
  }));
}

/**
 * Renders the Overview sub-tab from the same trading_update broadcast
 * payload every other Trading sub-tab reads (data.positions/total_pnl/
 * all_fills). No separate poll/route needed -- but trading_update fires
 * often, for reasons unrelated to this tab (any trading activity at all,
 * not just something the user is looking at). Rebuilding the whole
 * innerHTML on every call -- the original approach -- destroys and
 * recreates the canvas element each time, which silently killed any
 * in-progress hover and any highlight mid-render: looked like random
 * "glitchy" flicker, not tied cleanly to mouse movement, reported live
 * 2026-08-28. Fix: only rebuild the DOM when the actual SHAPE of what's
 * shown changes (which lines exist, empty vs populated, by-stock
 * placeholder vs real chart) -- tracked via a structural key stashed on
 * the mount element. An unchanged shape just updates the total's text
 * and repaints the existing canvases in place, leaving their DOM nodes
 * (and hover listeners) untouched.
 * @param {Object} data - trading_update's data
 * @param {HTMLElement} [container] - defaults to #trading-overview-mount
 * @param {Function} [sendEventFn] - wires the Reset button + archive
 *   expand-to-fetch (S37); omit for a read-only render (e.g. tests).
 */
function renderOverviewPanel(data, container, sendEventFn) {
  const mount = container || document.getElementById('trading-overview-mount');
  if (!mount) return;
  _lastOverviewRenderArgs = { data: data, sendEventFn: sendEventFn };
  _injectOverviewStyles();
  const strategies = (data && data.positions) || [];
  const totalPnl = (data && typeof data.total_pnl === 'number') ? data.total_pnl : null;
  const totalHtml = totalPnl === null
    ? '<span class="trd-overview-total-value">—</span>'
    : `<span class="trd-overview-total-value ${totalPnl >= 0 ? 'positive' : 'negative'}">$${totalPnl.toFixed(2)}</span>`;
  const resetBtnHtml = '<button class="trd-overview-reset-btn">Reset</button>';

  // Only strategies with at least one recorded fill get a line -- keep the
  // legend consistent with what _drawOverviewChart actually draws (it
  // filters the same way), not a legend entry with no matching line.
  const strategyLines = strategies.filter((s) => s.equity_curve && s.equity_curve.length > 0);
  // S35c/d: by-stock section. Degrades to a placeholder until all_fills
  // exists on the broadcast (backend not landed yet) -- the by-strategy
  // section above works fully already, this is purely additive.
  const allFills = (data && data.all_fills) || null;
  const symbolLines = allFills ? _groupFillsBySymbol(allFills) : [];
  // S37d: collapsible history of past reset_paper_trading blocks.
  const archives = (data && data.paper_archives) || [];
  const historyHtml = _renderArchiveHistory(archives);

  const structureKey = JSON.stringify({
    empty: strategies.length === 0,
    strategyNames: strategyLines.map((s) => s.name),
    byStock: allFills === null ? 'placeholder' : symbolLines.length === 0 ? 'empty' : symbolLines.map((s) => s.name),
    archives: archives.map((a) => [a.id, a.total_pnl, a.trade_count]),
    expanded: Object.keys(_archiveExpanded).sort(),
    fillsLoaded: Object.keys(_archiveFillsCache).filter((id) => _archiveFillsCache[id] !== 'loading').sort(),
  });
  // Fake-DOM test harness (no jsdom) never matches an existing element
  // here, so this always falls through to a full rebuild in tests --
  // consistent with every existing test's assertions against fresh
  // innerHTML output.
  const canReuse = typeof mount.querySelector === 'function'
    && mount._overviewStructureKey === structureKey
    && mount.querySelector('.trd-overview-total');

  if (canReuse) {
    const totalEl = mount.querySelector('.trd-overview-total');
    if (totalEl) totalEl.innerHTML = 'Total across all trades: ' + totalHtml;
    // Redrawing can shift a line's pixel coordinates (new data point,
    // rescaled axis) without moving the mouse -- force the next mousemove
    // to repaint a highlight rather than skipping it as "unchanged" per
    // _wireOverviewChartHover's own frame-dedupe (it would otherwise judge
    // "same line name, same rounded x" as nothing to do, leaving a stale
    // or just-cleared highlight on screen until the mouse actually moves).
    const strategyCanvas = mount.querySelector('#trd-overview-canvas');
    if (strategyCanvas) {
      strategyCanvas._overviewLastFrameKey = null;
      _drawOverviewChart(strategyCanvas, strategyLines);
    }
    const symbolCanvas = mount.querySelector('#trd-overview-canvas-symbol');
    if (symbolCanvas) {
      symbolCanvas._overviewLastFrameKey = null;
      _drawOverviewChart(symbolCanvas, symbolLines);
    }
    return;
  }
  mount._overviewStructureKey = structureKey;

  if (strategies.length === 0) {
    mount.innerHTML = `<div class="trd-overview">
      <div class="trd-overview-header">
        <div class="trd-overview-total">Total across all trades: ${totalHtml}</div>
        ${resetBtnHtml}
      </div>
      <div style="padding:16px; color:var(--text-muted); text-align:center;">No active strategies yet.</div>
      <h3 class="trd-overview-section-title">History</h3>
      <div class="trd-overview-history">${historyHtml}</div>
    </div>`;
    _wireOverviewControls(mount, sendEventFn);
    return;
  }

  const strategyLegend = strategyLines.map((s, i) => `
    <span class="trd-overview-legend-item">
      <span class="trd-overview-legend-swatch" style="background:${_OVERVIEW_LINE_COLORS[i % _OVERVIEW_LINE_COLORS.length]}"></span>
      ${s.name}
    </span>
  `).join('');
  const symbolLegend = symbolLines.map((s, i) => `
    <span class="trd-overview-legend-item">
      <span class="trd-overview-legend-swatch" style="background:${_OVERVIEW_LINE_COLORS[i % _OVERVIEW_LINE_COLORS.length]}"></span>
      ${s.name}
    </span>
  `).join('');
  const symbolSectionHtml = allFills === null
    ? '<div class="trd-overview-placeholder">Per-stock breakdown not available yet.</div>'
    : symbolLines.length === 0
      ? '<div class="trd-overview-placeholder">No fills yet.</div>'
      : `<canvas class="trd-overview-canvas" id="trd-overview-canvas-symbol"></canvas>
         <div class="trd-overview-legend">${symbolLegend}</div>`;

  mount.innerHTML = `<div class="trd-overview">
    <div class="trd-overview-header">
      <div class="trd-overview-total">Total across all trades: ${totalHtml}</div>
      ${resetBtnHtml}
    </div>
    <h3 class="trd-overview-section-title">By Strategy</h3>
    <canvas class="trd-overview-canvas" id="trd-overview-canvas"></canvas>
    <div class="trd-overview-legend">${strategyLegend}</div>
    <h3 class="trd-overview-section-title">By Stock</h3>
    ${symbolSectionHtml}
    <h3 class="trd-overview-section-title">History</h3>
    <div class="trd-overview-history">${historyHtml}</div>
  </div>`;

  // Same fake-DOM guard as the Tickers chart above -- no jsdom in this
  // repo's JS test suite, only innerHTML is exercised there.
  if (typeof mount.querySelector === 'function') {
    const strategyCanvas = mount.querySelector('#trd-overview-canvas');
    if (strategyCanvas) {
      _drawOverviewChart(strategyCanvas, strategyLines);
      _wireOverviewChartHover(strategyCanvas);
    }
    const symbolCanvas = mount.querySelector('#trd-overview-canvas-symbol');
    if (symbolCanvas) {
      _drawOverviewChart(symbolCanvas, symbolLines);
      _wireOverviewChartHover(symbolCanvas);
    }
  }
  _wireOverviewControls(mount, sendEventFn);
}

/* ── Trade Log sub-tab -- searchable/filterable fill history, split into
 * Paper and Live sections (user-requested 2026-08-28, same day as
 * Overview). Reuses data.all_fills (S35c/d) unchanged -- no new backend
 * needed, this is entirely a client-side view over data already
 * broadcast. Search/dropdown state is preserved across broadcasts (the
 * skeleton is built once and updated in place, same lesson as
 * renderOverviewPanel's own fix earlier this session) so typing a search
 * term doesn't get wiped out by the next unrelated trading_update. ────── */

function _tradeLogOptionsHtml(values, allLabel) {
  return ['<option value="">' + allLabel + '</option>'].concat(
    values.map((v) => `<option value="${v}">${v.length > 50 ? v.slice(0, 50) + '…' : v}</option>`)
  ).join('');
}

/**
 * Refreshes a `<select>`'s options from a fresh value list, preserving
 * the current selection if it's still a valid option -- a broadcast
 * landing mid-search must not silently reset a filter the user picked.
 */
function _refreshTradeLogSelect(select, values, allLabel) {
  if (!select) return;
  const prev = select.value;
  select.innerHTML = _tradeLogOptionsHtml(values, allLabel);
  const stillValid = values.indexOf(prev) !== -1;
  select.value = stillValid ? prev : '';
}

function _tradeLogFilterRows(fills, searchVal, strategyVal, symbolVal) {
  const search = (searchVal || '').toLowerCase();
  return fills.filter((f) => {
    if (strategyVal && f.strategy_id !== strategyVal) return false;
    if (symbolVal && f.symbol !== symbolVal) return false;
    if (search && (f.symbol + ' ' + f.strategy_id).toLowerCase().indexOf(search) === -1) return false;
    return true;
  });
}

function _renderTradeLogRows(section, fills) {
  const tbody = section.querySelector('.trd-log-tbody');
  const emptyEl = section.querySelector('.trd-log-empty');
  if (!tbody) return;
  const searchVal = section.querySelector('.trd-log-search').value;
  const strategyVal = section.querySelector('.trd-log-strategy-filter').value;
  const symbolVal = section.querySelector('.trd-log-symbol-filter').value;
  // Newest first for a log -- all_fills itself is chronological oldest-first
  // (S35c's own doc comment: needed that order to build a running curve).
  const rows = _tradeLogFilterRows(fills, searchVal, strategyVal, symbolVal).slice().reverse();

  if (rows.length === 0) {
    tbody.innerHTML = '';
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;
  tbody.innerHTML = rows.map((f) => `
    <tr>
      <td>${new Date(f.timestamp).toLocaleString()}</td>
      <td>${f.symbol}</td>
      <td class="trd-log-side-${f.side}">${f.side.toUpperCase()}</td>
      <td>${f.qty}</td>
      <td>$${f.price.toFixed(2)}</td>
      <td>$${f.fees.toFixed(2)}</td>
      <td class="${f.pnl >= 0 ? 'positive' : 'negative'}">$${f.pnl.toFixed(2)}</td>
      <td class="trd-log-strategy-cell" title="${f.strategy_id}">${f.strategy_id.length > 40 ? f.strategy_id.slice(0, 40) + '…' : f.strategy_id}</td>
    </tr>
  `).join('');
}

function _buildTradeLogSection(sectionId, title) {
  return `
    <div class="trd-log-section" data-log-section="${sectionId}">
      <h3 class="trd-overview-section-title">${title}</h3>
      <div class="trd-log-filters">
        <input type="text" class="trd-log-search" placeholder="Search symbol or strategy…">
        <select class="trd-log-strategy-filter"><option value="">All strategies</option></select>
        <select class="trd-log-symbol-filter"><option value="">All symbols</option></select>
      </div>
      <div class="trd-log-table-wrap">
        <table class="trd-log-table">
          <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Fees</th><th>PnL</th><th>Strategy</th></tr></thead>
          <tbody class="trd-log-tbody"></tbody>
        </table>
      </div>
      <div class="trd-log-empty">No trades match.</div>
    </div>
  `;
}

function _wireTradeLogSection(mount, sectionId, getFills) {
  const section = mount.querySelector('[data-log-section="' + sectionId + '"]');
  if (!section) return;
  const rerender = () => _renderTradeLogRows(section, getFills());
  section.querySelector('.trd-log-search').addEventListener('input', rerender);
  section.querySelector('.trd-log-strategy-filter').addEventListener('change', rerender);
  section.querySelector('.trd-log-symbol-filter').addEventListener('change', rerender);
}

function _injectTradeLogStyles() {
  const styleId = 'trading-log-styles';
  if (document.getElementById(styleId)) return;
  const style = document.createElement('style');
  style.id = styleId;
  style.textContent = `
    .trd-log { padding: 12px; }
    .trd-log-section { margin-bottom: 24px; }
    .trd-log-filters { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
    .trd-log-search { flex: 1; min-width: 160px; padding: 4px 8px; border: 1px solid var(--border, #ccc); border-radius: 4px; background: var(--bg, #fff); color: var(--text, #333); }
    .trd-log-strategy-filter, .trd-log-symbol-filter { padding: 4px 8px; border: 1px solid var(--border, #ccc); border-radius: 4px; background: var(--bg, #fff); color: var(--text, #333); max-width: 220px; }
    .trd-log-table-wrap { overflow-x: auto; }
    .trd-log-table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
    .trd-log-table th, .trd-log-table td { padding: 5px 8px; border-bottom: 1px solid var(--border, #eee); text-align: left; white-space: nowrap; }
    .trd-log-side-buy { color: #2ecc71; }
    .trd-log-side-sell { color: #e74c3c; }
    .trd-log-strategy-cell { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .trd-log-empty { padding: 12px; color: var(--text-muted); text-align: center; font-size: 0.9em; }
  `;
  document.head.appendChild(style);
}

/**
 * Renders the Trade Log sub-tab: two searchable/filterable sections
 * (Paper, Live) over data.all_fills, split by `phase`. Builds the
 * skeleton once and updates rows/dropdown-options in place on later
 * calls (trading_update fires often, for reasons unrelated to this tab)
 * so an in-progress search isn't wiped out by an unrelated broadcast.
 * @param {Object} data - trading_update's data
 * @param {HTMLElement} [container] - defaults to #trading-log-mount
 */
function renderTradeLog(data, container) {
  const mount = container || document.getElementById('trading-log-mount');
  if (!mount) return;
  _injectTradeLogStyles();
  const allFills = (data && data.all_fills) || null;

  if (allFills === null) {
    if (typeof mount.querySelector !== 'function' || !mount.querySelector('.trd-log')) {
      mount.innerHTML = '<div class="trd-log"><div class="trd-log-empty">Trade log not available yet.</div></div>';
    }
    return;
  }

  const paperFills = allFills.filter((f) => f.phase === 'paper');
  const liveFills = allFills.filter((f) => f.phase === 'live');
  mount._logPaperFills = paperFills;
  mount._logLiveFills = liveFills;

  const alreadyBuilt = typeof mount.querySelector === 'function' && mount.querySelector('.trd-log');
  if (!alreadyBuilt) {
    mount.innerHTML = `<div class="trd-log">
      ${_buildTradeLogSection('paper', 'Paper Trades')}
      ${_buildTradeLogSection('live', 'Live Trades')}
    </div>`;
    if (typeof mount.querySelector === 'function') {
      _wireTradeLogSection(mount, 'paper', () => mount._logPaperFills || []);
      _wireTradeLogSection(mount, 'live', () => mount._logLiveFills || []);
    }
  }

  if (typeof mount.querySelector !== 'function') return;
  const paperSection = mount.querySelector('[data-log-section="paper"]');
  const liveSection = mount.querySelector('[data-log-section="live"]');
  if (paperSection) {
    _refreshTradeLogSelect(paperSection.querySelector('.trd-log-strategy-filter'),
      Array.from(new Set(paperFills.map((f) => f.strategy_id))).sort(), 'All strategies');
    _refreshTradeLogSelect(paperSection.querySelector('.trd-log-symbol-filter'),
      Array.from(new Set(paperFills.map((f) => f.symbol))).sort(), 'All symbols');
    _renderTradeLogRows(paperSection, paperFills);
  }
  if (liveSection) {
    _refreshTradeLogSelect(liveSection.querySelector('.trd-log-strategy-filter'),
      Array.from(new Set(liveFills.map((f) => f.strategy_id))).sort(), 'All strategies');
    _refreshTradeLogSelect(liveSection.querySelector('.trd-log-symbol-filter'),
      Array.from(new Set(liveFills.map((f) => f.symbol))).sort(), 'All symbols');
    _renderTradeLogRows(liveSection, liveFills);
  }
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
  renderOverviewPanel: renderOverviewPanel,
  receiveArchiveFills: receiveArchiveFills,
  renderTradeLog: renderTradeLog,
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
