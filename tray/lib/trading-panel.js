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
  if (!data || !data.positions || data.positions.length === 0) {
    mount.innerHTML = '<div style="padding:16px; color:var(--text-muted); text-align:center;">No active strategies. Create one via the Scheduler or Strategy Gauntlet.</div>';
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

  mount.innerHTML = `
    <div class="trading-panel-layout">
      <div class="strategy-list">
        <h3>Strategies</h3>
        <ul>
          ${state.strategies.map((s, i) => `
            <li class="${i === state.selectedIdx ? 'selected' : ''}" data-idx="${i}">
              ${s.name} <span class="status-badge">${s.status.toUpperCase()}</span>
            </li>
          `).join('')}
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

  // Wire list selection
  mount.querySelectorAll('.strategy-list li').forEach(li => {
    li.addEventListener('click', () => {
      state.selectedIdx = parseInt(li.dataset.idx, 10);
      renderTradingUpdate(data, mount, sendEventFn);
    });
  });

  // Wire edit save button
  const saveBtn = mount.querySelector('.save-strategy-btn');
  const textarea = mount.querySelector('.strategy-code-editor');
  if (saveBtn && textarea && strategy) {
    saveBtn.addEventListener('click', () => {
      if (sendEventFn) sendEventFn(buildStrategyEditEvent(strategy, textarea.value));
    });
  }

  // Inject styles (idempotent)
  const styleId = "trading-panel-v2-styles";
  if (!document.getElementById(styleId)) {
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
      .fill-list, .alerts-box { margin-top: 16px; }
      .fills-table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
      .fills-table th, .fills-table td { padding: 4px 6px; border: 1px solid #eee; text-align: left; }
      .alerts-box ul { list-style: none; padding: 0; }
      .alerts-box li { padding: 4px 0; border-bottom: 1px solid #f0f0f0; font-size: 0.9em; }
      .alert-critical { color: #e74c3c; }
      .alert-warning { color: #f39c12; }
      .alert-info { color: #3498db; }
    `;
    document.head.appendChild(style);
  }
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
  if (stage === 'validated') return 'Validated';
  return 'Charting';
}

/**
 * Renders one ticker's card markup. Three stages (decision #49) -- a
 * screened ticker with no strategy yet gets a one-line status, a validated
 * strategy with zero fills gets a one-line status, and only a strategy with
 * at least one fill gets a canvas (one per phase segment -- decision #50
 * keeps paper/live as separate, never-joined charts).
 */
function _renderTickerCard(ticker, ti) {
  const strategies = ticker.strategies || [];
  let body;
  if (ticker.stage === 'screened' || strategies.length === 0) {
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
    .trd-ticker-status { color: var(--text-muted); font-size: 0.9em; margin: 4px 0; }
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
  renderStrategyCard:  renderStrategyCard,
  renderLiveStrategyCard: renderLiveStrategyCard,
  buildStrategyEditEvent: buildStrategyEditEvent,
  buildRunGauntletEvent: buildRunGauntletEvent,
  initTickersView:     initTickersView,
  renderTickersUpdate: renderTickersUpdate,
};

}));
