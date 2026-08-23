/**
 * Wires up the Trading Panel UI in the tray.
 * Fetches initial state, renders, and listens for live updates via IPC.
 */
export function initTradingPanel() {
  const mount = document.getElementById('trading-panel-mount');
  if (!mount) return;

  const _renderState = (state) => {
    if (!state || !state.positions || state.positions.length === 0) {
      mount.innerHTML = '<div style="padding:16px; color:var(--text-muted); text-align:center;">No active strategies. Create one via the Scheduler or Strategy Gauntlet.</div>';
      return;
    }
    // Render the first live/paper strategy card
    const data = state.positions[0];
    renderLiveStrategyCard(data, mount);
  };

  // Initial fetch
  window.sendEvent({ type: 'trading_poll' });

  // Listen for live updates
  window.addEventListener('message', (evt) => {
    if (evt.data?.type === 'trading_update') {
      _renderState(evt.data.data);
    }
  });
}

/**
 * Renders a StrategyCard into the given container element.
 * @param {Object} card - StrategyCard object from gauntlet
 * @param {HTMLElement} container - DOM element to render into
 */
export function renderStrategyCard(card, container) {
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
export function renderLiveStrategyCard(data, container) {
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
          <li>Live Trades: ${data.live_trades}</li>
          <li>Equity Curve: ${data.equity_curve ? '📈' : '⏳'}</li>
        </ul>
      </div>
      <div class="card-section">
        <h3>Recent Fills</h3>
        <table class="fills-table">
          <thead><tr><th>Symbol</th><th>Side</th><th>PnL</th></tr></thead>
          <tbody>
            ${(data.fills || []).slice(0, 5).map(f => `
              <tr>
                <td>${f.symbol}</td>
                <td>${f.side.toUpperCase()}</td>
                <td>${f.pnl.toFixed(2)}</td>
              </tr>
            `).join("") || '<tr><td colspan="3">No fills yet</td></tr>'}
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
      .lifecycle-badge { font-size: 0.75em; padding: 2px 6px; border-radius: 4px; background: #eee; text-transform: uppercase; }
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
