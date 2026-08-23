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

// S19 (#864): multi-strategy list, provenance/version rendering, and the
// edit box's real event shape. renderTradingUpdate itself calls
// mount.querySelectorAll/querySelector after setting innerHTML (to wire the
// list-click and Save handlers) -- fakeInteractiveMount below stubs those as
// no-ops (this suite has no jsdom to make them real) so the function doesn't
// throw; what actually gets rendered is verified against the innerHTML
// string itself, matching this file's own established assertion style.

function fakeInteractiveMount() {
  return {
    innerHTML: '',
    querySelectorAll: () => ({ forEach: () => {} }),
    querySelector: () => null,
  };
}

const TWO_STRATEGIES = [
  {
    name: 'MA cross A', status: 'paper', version: 2,
    provenance: 'MA cross trend test, as modified by user (v2)',
    code: 'def strategy(data):\n    return [1]\n',
    recent_fills: [], equity_curve: [],
  },
  {
    name: 'MA cross B', status: 'live', version: 1,
    provenance: 'generated (v1)',
    code: 'def strategy(data):\n    return [0]\n',
    recent_fills: [], equity_curve: [],
  },
];

describe('renderTradingUpdate (S19 multi-strategy panel)', () => {
  test('renders every strategy in the list, not just positions[0]', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: TWO_STRATEGIES, alerts: [] }, mount);
      expect(mount.innerHTML).toContain('MA cross A');
      expect(mount.innerHTML).toContain('MA cross B');
    });
  });

  test('provenance and version are actually rendered, not just present in the data', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: TWO_STRATEGIES, alerts: [] }, mount);
      // The selected (first) strategy's detail pane is what's visible.
      expect(mount.innerHTML).toContain('v2');
      expect(mount.innerHTML).toContain('MA cross trend test, as modified by user');
      expect(mount.innerHTML).toContain('def strategy(data):');
    });
  });

  test('a strategy with no lineage shows a plain fallback, not blank/undefined', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      const noLineage = [{ ...TWO_STRATEGIES[0], provenance: '', version: 0, code: '' }];
      TradingPanel.renderTradingUpdate({ positions: noLineage, alerts: [] }, mount);
      expect(mount.innerHTML).toContain('No lineage recorded.');
      expect(mount.innerHTML).not.toContain('undefined');
    });
  });
});

describe('buildStrategyEditEvent (the Save button\'s real event shape)', () => {
  test('sends strategy_edit with the strategy name, new code, and its version', () => {
    const event = TradingPanel.buildStrategyEditEvent(
      { name: 'MA cross A', version: 2 }, 'def strategy(data):\n    return [1, 0]\n'
    );
    expect(event).toEqual({
      type: 'strategy_edit',
      data: {
        strategy_name: 'MA cross A',
        code: 'def strategy(data):\n    return [1, 0]\n',
        version: 2,
      },
    });
  });
});

describe('buildRunGauntletEvent (the create-strategy form\'s real event shape)', () => {
  const BASE_FIELDS = { symbol: 'AAPL', hypothesis: 'MA cross beats buy-and-hold' };

  test('code source: reuses the generic call_tool WS route with run_gauntlet', () => {
    const event = TradingPanel.buildRunGauntletEvent({
      ...BASE_FIELDS, source: 'code', code: 'def strategy(data):\n    return [1]\n',
    });
    expect(event).toEqual({
      type: 'call_tool',
      data: {
        name: 'run_gauntlet',
        args: {
          symbol: 'AAPL',
          hypothesis: 'MA cross beats buy-and-hold',
          code: 'def strategy(data):\n    return [1]\n',
        },
      },
    });
  });

  test('claim source: sends claim, not code/url/book', () => {
    const event = TradingPanel.buildRunGauntletEvent({
      ...BASE_FIELDS, source: 'claim', claim: 'Buy when RSI crosses 30',
    });
    expect(event.data.args).toEqual({
      symbol: 'AAPL', hypothesis: 'MA cross beats buy-and-hold',
      claim: 'Buy when RSI crosses 30',
    });
  });

  test('url source: sends url only', () => {
    const event = TradingPanel.buildRunGauntletEvent({
      ...BASE_FIELDS, source: 'url', url: 'https://example.com/strategy',
    });
    expect(event.data.args).toEqual({
      symbol: 'AAPL', hypothesis: 'MA cross beats buy-and-hold',
      url: 'https://example.com/strategy',
    });
  });

  test('book source: sends both book and chapter', () => {
    const event = TradingPanel.buildRunGauntletEvent({
      ...BASE_FIELDS, source: 'book', book: 'Market Wizards', chapter: '3',
    });
    expect(event.data.args).toEqual({
      symbol: 'AAPL', hypothesis: 'MA cross beats buy-and-hold',
      book: 'Market Wizards', chapter: '3',
    });
  });

  test('optional provenance is included only when given', () => {
    const withProv = TradingPanel.buildRunGauntletEvent({
      ...BASE_FIELDS, source: 'code', code: 'x', provenance: 'user, verbatim',
    });
    expect(withProv.data.args.provenance).toBe('user, verbatim');

    const withoutProv = TradingPanel.buildRunGauntletEvent({
      ...BASE_FIELDS, source: 'code', code: 'x',
    });
    expect(withoutProv.data.args.provenance).toBeUndefined();
  });
});
