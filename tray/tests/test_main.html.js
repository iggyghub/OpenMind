const { JSDOM } = require('jsdom');

let dom;
let document;
let window;

beforeEach(() => {
  dom = new JSDOM(`
    <!DOCTYPE html>
    <html>
      <head>
        <style>
          :root {
            --border: #2a2640;
            --state-active: #4bd49a;
            --text-dim: #a8a4c8;
            --text-muted: #6b6790;
            --color-success: #4bd49a;
          }
          #active-systems { display: block; }
          .active-sys-header { font-size: 10px; color: var(--state-active); }
          .active-sys-row { display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
          .active-sys-row[data-status="running"] { color: var(--color-success); }
          .active-sys-row[data-status="idle"] { color: var(--text-muted); }
          .active-sys-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-muted); }
          .active-sys-dot.is-running { background: var(--color-success); }
          .active-sys-dot.is-idle { background: var(--text-muted); }
        </style>
      </head>
      <body>
        <div id="active-systems"></div>
      </body>
    </html>
  `);
  window = dom.window;
  document = window.document;
  global.document = document;
  global.window = window;

  // Inject the core logic being tested
  eval(`
    const activeSystemsState = { batchReplay: null, crossStockSweep: null };
    function renderActiveSystems() {
      const container = document.getElementById('active-systems');
      if (!container) return;
      let html = '<div class="active-sys-header">Active Systems</div>';
      const systems = [
        { key: 'batchReplay', label: 'Batch Replay' },
        { key: 'crossStockSweep', label: 'Cross-Stock Sweep' }
      ];
      systems.forEach(sys => {
        const status = activeSystemsState[sys.key];
        const isRunning = status === 'running';
        html += \`
          <div class="active-sys-row" data-status="\${isRunning ? 'running' : 'idle'}">
            <span>\${sys.label}</span>
            <span class="active-sys-dot \${isRunning ? 'is-running' : 'is-idle'}"></span>
          </div>\`;
      });
      container.innerHTML = html;
    }
  `);
});

test('renderActiveSystems initializes with idle status', () => {
  renderActiveSystems();
  const rows = document.querySelectorAll('.active-sys-row');
  expect(rows).toHaveLength(2);
  rows.forEach(row => {
    expect(row.getAttribute('data-status')).toBe('idle');
  });
});

test('renderActiveSystems updates to running status', () => {
  activeSystemsState.batchReplay = 'running';
  activeSystemsState.crossStockSweep = 'idle';
  renderActiveSystems();
  const rows = document.querySelectorAll('.active-sys-row');
  expect(rows[0].getAttribute('data-status')).toBe('running');
  expect(rows[1].getAttribute('data-status')).toBe('idle');
});

test('pollActiveSystems dispatches custom event with record:false on init', () => {
  const handler = jest.fn();
  window.addEventListener('request-active-systems', handler);
  pollActiveSystems(false);
  expect(handler).toHaveBeenCalledTimes(1);
  expect(handler.mock.calls[0][0].detail.record).toBe(false);
});

test('tool_result patch updates state and triggers render', () => {
  renderActiveSystems();
  const msg = {
    content: { tool_name: 'get_batch_replay_status', result: 'running' }
  };
  // Simulate the patched handler
  window.handleToolResult(msg);
  const rows = document.querySelectorAll('.active-sys-row');
  expect(rows[0].getAttribute('data-status')).toBe('running');
  expect(activeSystemsState.batchReplay).toBe('running');
});
