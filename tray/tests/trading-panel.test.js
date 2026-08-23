'use strict';

// Unit tests for tray/lib/trading-panel.js -- specifically renderLiveStrategyCard's
// fills table (S12/#856 Part B: phase must be visibly rendered, not just present
// in the broadcast data). testEnvironment is "node" project-wide (no jsdom
// dependency installed) -- a minimal fake `document`/container stand in for the
// DOM APIs renderLiveStrategyCard actually touches (innerHTML assignment, plus
// the one-time <style> injection it does via document.getElementById/
// createElement/head.appendChild).

const TradingPanel = require('../lib/trading-panel');

function fakeContainer() {
  return { innerHTML: '' };
}

function withFakeDocument(fn) {
  const styleEls = [];
  global.document = {
    getElementById: () => null, // style tag never yet injected
    createElement: () => ({ set textContent(_v) {}, id: '' }),
    head: { appendChild: (el) => styleEls.push(el) },
  };
  try {
    fn();
  } finally {
    delete global.document;
  }
}

const BASE_DATA = {
  name: 'penny breakout', status: 'live', live_trades: 5, equity_curve: [1, 2, 3],
  alerts: [],
};

describe('renderLiveStrategyCard fills table', () => {
  test('reads data.recent_fills (the real _trading_broadcast key), not data.fills', () => {
    withFakeDocument(() => {
      const container = fakeContainer();
      const data = {
        ...BASE_DATA,
        recent_fills: [{ symbol: 'AAPL', side: 'buy', pnl: 12.5, phase: 'paper' }],
      };
      TradingPanel.renderLiveStrategyCard(data, container);
      expect(container.innerHTML).toContain('AAPL');
      expect(container.innerHTML).toContain('12.50');
      expect(container.innerHTML).not.toContain('No fills yet');
    });
  });

  test('a live fill renders a visible LIVE phase badge, distinct from paper', () => {
    withFakeDocument(() => {
      const container = fakeContainer();
      const data = {
        ...BASE_DATA,
        recent_fills: [
          { symbol: 'AAPL', side: 'sell', pnl: -3.0, phase: 'live' },
          { symbol: 'TSLA', side: 'buy', pnl: 4.0, phase: 'paper' },
        ],
      };
      TradingPanel.renderLiveStrategyCard(data, container);
      expect(container.innerHTML).toContain('phase-badge live');
      expect(container.innerHTML).toContain('phase-badge paper');
      expect(container.innerHTML).toContain('>LIVE<');
      expect(container.innerHTML).toContain('>PAPER<');
    });
  });

  test('no fills still renders the empty-state row, not a crash', () => {
    withFakeDocument(() => {
      const container = fakeContainer();
      TradingPanel.renderLiveStrategyCard({ ...BASE_DATA, recent_fills: [] }, container);
      expect(container.innerHTML).toContain('No fills yet');
    });
  });
});
