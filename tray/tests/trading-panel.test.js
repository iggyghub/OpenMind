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

// S32/#898 (2026-08-27): user feedback -- no way to tell if a strategy is
// actually trading (list showed only name + status) and no way to stop
// one manually (only the automatic CI/drawdown halt existed).
describe('renderTradingUpdate trade-count badge (S32/#898)', () => {
  test('shows the real live_trades count in the list', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      const strategies = [{ ...TWO_STRATEGIES[0], live_trades: 7 }];
      TradingPanel.renderTradingUpdate({ positions: strategies, alerts: [] }, mount);
      expect(mount.innerHTML).toContain('7 trades');
    });
  });

  test('a strategy with no trades yet shows "0 trades", not undefined', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      const strategies = [{ ...TWO_STRATEGIES[0], live_trades: 0 }];
      TradingPanel.renderTradingUpdate({ positions: strategies, alerts: [] }, mount);
      expect(mount.innerHTML).toContain('0 trades');
      expect(mount.innerHTML).not.toContain('undefined');
    });
  });

  test('a missing live_trades field defaults to 0 rather than rendering undefined', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: TWO_STRATEGIES, alerts: [] }, mount);
      expect(mount.innerHTML).not.toContain('undefined');
    });
  });

  test('exactly one trade is singular, not "1 trades"', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      const strategies = [{ ...TWO_STRATEGIES[0], live_trades: 1 }];
      TradingPanel.renderTradingUpdate({ positions: strategies, alerts: [] }, mount);
      expect(mount.innerHTML).toContain('1 trade<');
      expect(mount.innerHTML).not.toContain('1 trades');
    });
  });
});

describe('buildHaltStrategyEvent / buildResumeStrategyEvent (S32/#898)', () => {
  test('halt_strategy carries the strategy id', () => {
    expect(TradingPanel.buildHaltStrategyEvent('my strategy@v1')).toEqual({
      type: 'call_tool', data: { name: 'halt_strategy', args: { strategy_id: 'my strategy@v1' } },
    });
  });

  test('resume_strategy carries the strategy id', () => {
    expect(TradingPanel.buildResumeStrategyEvent('my strategy@v1')).toEqual({
      type: 'call_tool', data: { name: 'resume_strategy', args: { strategy_id: 'my strategy@v1' } },
    });
  });
});

describe('renderTradingUpdate Halt/Resume button (S32/#898)', () => {
  test('an active (non-halted) strategy shows Halt', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: [{ ...TWO_STRATEGIES[0], status: 'paper' }], alerts: [] }, mount);
      expect(mount.innerHTML).toContain('>Halt<');
      expect(mount.innerHTML).not.toContain('>Resume<');
    });
  });

  test('a halted strategy shows Resume, not Halt', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: [{ ...TWO_STRATEGIES[0], status: 'halted' }], alerts: [] }, mount);
      expect(mount.innerHTML).toContain('>Resume<');
      expect(mount.innerHTML).not.toContain('>Halt<');
    });
  });
});

// S31 (#896): manual discovery start/stop + duration control. The control
// must render on BOTH the empty-positions and populated branches (it's
// independent of whether any strategy exists yet) -- that's the real bug
// class this suite guards against, not just "does the button show up."
describe('renderTradingUpdate discovery control (S31/#896)', () => {
  test('renders even with zero strategies -- not hidden behind the empty state', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: [], alerts: [], discovery: { enabled: false } }, mount);
      expect(mount.innerHTML).toContain('No active strategies');
      expect(mount.innerHTML).toContain('Autonomous Discovery');
      expect(mount.innerHTML).toContain('discovery-start-btn');
      expect(mount.innerHTML).toContain('discovery-stop-btn');
    });
  });

  test('renders alongside a populated strategy list too', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: TWO_STRATEGIES, alerts: [], discovery: { enabled: false } }, mount);
      expect(mount.innerHTML).toContain('Autonomous Discovery');
      expect(mount.innerHTML).toContain('MA cross A');
    });
  });

  test('disabled discovery shows Stopped, Start enabled, Stop disabled', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: [], alerts: [], discovery: { enabled: false } }, mount);
      expect(mount.innerHTML).toContain('Stopped');
      expect(mount.innerHTML).toContain('discovery-start-btn" >');
      expect(mount.innerHTML).toContain('discovery-stop-btn" disabled>');
    });
  });

  test('enabled discovery with no stop_at shows "Running indefinitely", Start disabled, Stop enabled', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: [], alerts: [], discovery: { enabled: true, stop_at: '' } }, mount);
      expect(mount.innerHTML).toContain('Running indefinitely');
      expect(mount.innerHTML).toContain('discovery-start-btn" disabled>');
      expect(mount.innerHTML).toContain('discovery-stop-btn" >');
    });
  });

  test('enabled discovery with a stop_at shows a real stop time, not just "Running"', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({
        positions: [], alerts: [],
        discovery: { enabled: true, stop_at: '2026-08-26T00:00:00+00:00' },
      }, mount);
      expect(mount.innerHTML).toContain('Running -- stops');
      expect(mount.innerHTML).not.toContain('Running indefinitely');
    });
  });

  test('missing discovery data entirely renders as stopped, not a crash', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: [], alerts: [] }, mount);
      expect(mount.innerHTML).toContain('Stopped');
    });
  });
});

// 2026-08-27: Books moved out to its own Trading sub-tab (previously
// embedded atop the Strategies sub-tab, see renderTradingUpdate below for
// the "it's gone from there now" regression guard) -- renderBooksPanel
// reads the exact same trading_update payload, just into its own mount.
describe('renderBooksPanel (2026-08-27, was "renderTradingUpdate books section")', () => {
  test('renders even with zero strategies and no books uploaded yet', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({ positions: [], alerts: [] }, mount);
      expect(mount.innerHTML).toContain('Books');
      expect(mount.innerHTML).toContain('books-file-input');
      expect(mount.innerHTML).toContain('No books uploaded yet.');
    });
  });

  test('shows which model is reading books, when known', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [], books: [], books_model: 'Budd thinking',
      }, mount);
      expect(mount.innerHTML).toContain('reading with Budd thinking');
    });
  });

  test('omits the reading-with label when no books_model is given', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({ positions: [], alerts: [], books: [] }, mount);
      expect(mount.innerHTML).not.toContain('reading with');
    });
  });

  test('a processing book shows a progress bar and chunk count', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 1, title: 'Reminiscences', filename: 'r.pdf', status: 'processing', total_chunks: 20, processed_chunks: 8, strategies_found: 1, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).toContain('book-progress-bar');
      expect(mount.innerHTML).toContain('width:40%');
      expect(mount.innerHTML).toContain('8/20 chunks');
    });
  });

  test('a done book does not show a progress bar', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 1, title: 'Done Book', filename: 'd.pdf', status: 'done', total_chunks: 5, processed_chunks: 5, strategies_found: 2, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).not.toContain('book-progress-bar');
    });
  });

  // 2026-08-27: "N strategies found" was actually every gauntlet dispatch
  // attempt (pass or fail) -- confusingly high (e.g. "190 strategies
  // found" against 3 real validated ones). Now split into a plain
  // dispatch count and a real "N valid strategies" figure with a
  // drill-down into what they actually are.
  test('shows dispatch count separately from the real valid-strategy count', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{
          id: 1, title: 'The Intelligent Investor', filename: 'ii.mobi', status: 'processing',
          total_chunks: 205, processed_chunks: 42, strategies_found: 190, error_message: '',
          valid_strategies: [
            { strategy_id: 's1', symbol: 'AAPL', hypothesis: 'Buy Dow dogs by yield/sqrt(price)', chapter: 'chunk 16', version: 1, code: 'def strategy(data): ...', created_at: '2026-08-27T00:16:00Z' },
          ],
        }],
      }, mount);
      expect(mount.innerHTML).toContain('190 dispatches');
      expect(mount.innerHTML).toContain('1 valid strategy');
      expect(mount.innerHTML).not.toContain('190 strategies found');
    });
  });

  test('a book with zero valid strategies shows a disabled toggle', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 1, title: 'No Hits Yet', filename: 'x.pdf', status: 'processing', total_chunks: 5, processed_chunks: 1, strategies_found: 0, error_message: '', valid_strategies: [] }],
      }, mount);
      expect(mount.innerHTML).toContain('0 valid strategies');
      expect(mount.innerHTML).toMatch(/book-valid-toggle[^>]*disabled/);
    });
  });

  test('valid strategy details (symbol, hypothesis, chapter) are present in the DOM for drill-down', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{
          id: 1, title: 'Market Wizards', filename: 'mw.pdf', status: 'done',
          total_chunks: 10, processed_chunks: 10, strategies_found: 20, error_message: '',
          valid_strategies: [
            { strategy_id: 's1', symbol: 'AAPL', hypothesis: 'A real extracted claim', chapter: 'chunk 3', version: 1, code: '...', created_at: '2026-08-27T00:00:00Z' },
          ],
        }],
      }, mount);
      expect(mount.innerHTML).toContain('book-valid-list');
      expect(mount.innerHTML).toContain('A real extracted claim');
      expect(mount.innerHTML).toContain('chunk 3');
    });
  });

  test('an errored book surfaces the real error message, not just "error"', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 1, title: 'Bad Book', filename: 'b.epub', status: 'error', total_chunks: 0, processed_chunks: 0, strategies_found: 0, error_message: 'Could not extract any text' }],
      }, mount);
      expect(mount.innerHTML).toContain('Error: Could not extract any text');
    });
  });

  test('a processing book shows a Stop button', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 5, title: 'Reminiscences', filename: 'r.pdf', status: 'processing', total_chunks: 20, processed_chunks: 8, strategies_found: 1, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).toContain('book-stop-btn');
      expect(mount.innerHTML).toContain('data-book-id="5"');
    });
  });

  test('a done book has no Stop button, but keeps Redo and Delete', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 5, title: 'Done Book', filename: 'd.pdf', status: 'done', total_chunks: 5, processed_chunks: 5, strategies_found: 2, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).not.toContain('book-stop-btn');
      expect(mount.innerHTML).toContain('book-retry-btn');
      expect(mount.innerHTML).toContain('book-delete-btn');
    });
  });

  // S33/#900 (2026-08-28): real pause/resume -- Resume continues from
  // processed_chunks, distinct from Redo's always-restart-from-0.
  test('a stopped book shows a Resume button alongside Redo', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 5, title: 'Paused Book', filename: 'p.pdf', status: 'stopped', total_chunks: 20, processed_chunks: 8, strategies_found: 1, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).toContain('book-resume-btn');
      expect(mount.innerHTML).toContain('book-retry-btn');
    });
  });

  test('a processing book has no Resume button (nothing to resume yet)', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 5, title: 'Active Book', filename: 'a.pdf', status: 'processing', total_chunks: 20, processed_chunks: 8, strategies_found: 1, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).not.toContain('book-resume-btn');
    });
  });

  test('a done book has no Resume button', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [{ id: 5, title: 'Done Book', filename: 'd.pdf', status: 'done', total_chunks: 5, processed_chunks: 5, strategies_found: 2, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).not.toContain('book-resume-btn');
    });
  });

  test('multiple books each render their own row', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({
        positions: [], alerts: [],
        books: [
          { id: 1, title: 'Book One', filename: 'a.pdf', status: 'done', total_chunks: 1, processed_chunks: 1, strategies_found: 0, error_message: '' },
          { id: 2, title: 'Book Two', filename: 'b.pdf', status: 'queued', total_chunks: 0, processed_chunks: 0, strategies_found: 0, error_message: '' },
        ],
      }, mount);
      expect(mount.innerHTML).toContain('Book One');
      expect(mount.innerHTML).toContain('Book Two');
      expect((mount.innerHTML.match(/book-row"/g) || []).length).toBe(2);
    });
  });
});

// 2026-08-28: once a handful of books finish, a flat list buries whatever's
// still active. Finished books collapse into a native <details>, active
// ones stay in the always-visible list.
describe('renderBooksPanel finished-books collapsible section (2026-08-28)', () => {
  const DONE_BOOK = { id: 1, title: 'Finished Book', filename: 'f.pdf', status: 'done', total_chunks: 10, processed_chunks: 10, strategies_found: 2, error_message: '' };
  const ACTIVE_BOOK = { id: 2, title: 'Active Book', filename: 'a.pdf', status: 'processing', total_chunks: 10, processed_chunks: 3, strategies_found: 0, error_message: '' };

  test('a done book is wrapped in the collapsible finished-books section', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({ positions: [], alerts: [], books: [DONE_BOOK] }, mount);
      expect(mount.innerHTML).toContain('books-done-section');
      expect(mount.innerHTML).toContain('1 finished book<');
      expect(mount.innerHTML).toContain('Finished Book');
    });
  });

  test('an active book is not inside the collapsible section', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({ positions: [], alerts: [], books: [ACTIVE_BOOK] }, mount);
      expect(mount.innerHTML).not.toContain('books-done-section');
      expect(mount.innerHTML).toContain('Active Book');
    });
  });

  test('active and done books both render, done ones only inside the collapsible section', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({ positions: [], alerts: [], books: [DONE_BOOK, ACTIVE_BOOK] }, mount);
      expect(mount.innerHTML).toContain('Active Book');
      expect(mount.innerHTML).toContain('Finished Book');
      expect((mount.innerHTML.match(/book-row"/g) || []).length).toBe(2);
      // Finished Book's row must appear AFTER the <details> opening tag,
      // Active Book's must appear BEFORE it -- confirms which section each landed in.
      const detailsIdx = mount.innerHTML.indexOf('books-done-section');
      expect(mount.innerHTML.indexOf('Active Book')).toBeLessThan(detailsIdx);
      expect(mount.innerHTML.indexOf('Finished Book')).toBeGreaterThan(detailsIdx);
    });
  });

  test('multiple finished books share one summary count', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      const second = { ...DONE_BOOK, id: 3, title: 'Second Finished Book' };
      TradingPanel.renderBooksPanel({ positions: [], alerts: [], books: [DONE_BOOK, second] }, mount);
      expect(mount.innerHTML).toContain('2 finished books<');
    });
  });

  test('no finished books yet -- no collapsible section rendered at all', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderBooksPanel({ positions: [], alerts: [], books: [ACTIVE_BOOK] }, mount);
      expect(mount.innerHTML).not.toContain('finished book');
    });
  });
});

describe('renderTradingUpdate no longer embeds the Books section (2026-08-27)', () => {
  test('the Strategies mount has no books UI even when books are present', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({
        positions: [], alerts: [],
        books: [{ id: 1, title: 'Market Wizards', filename: 'wiz.pdf', status: 'done', total_chunks: 10, processed_chunks: 10, strategies_found: 3, error_message: '' }],
      }, mount);
      expect(mount.innerHTML).not.toContain('books-file-input');
      expect(mount.innerHTML).not.toContain('Market Wizards');
    });
  });

  test('renders normally alongside a populated strategy list', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTradingUpdate({ positions: TWO_STRATEGIES, alerts: [] }, mount);
      expect(mount.innerHTML).toContain('MA cross A');
    });
  });
});

describe('buildUploadBookEvent (2026-08-26)', () => {
  test('builds the real upload_book call_tool shape', () => {
    expect(TradingPanel.buildUploadBookEvent('wizards.pdf', 'YWJj')).toEqual({
      type: 'call_tool',
      data: { name: 'upload_book', args: { filename: 'wizards.pdf', data_base64: 'YWJj' } },
    });
  });

  test('an explicit title is included; omitted defaults server-side', () => {
    expect(TradingPanel.buildUploadBookEvent('wizards.pdf', 'YWJj', 'Market Wizards')).toEqual({
      type: 'call_tool',
      data: { name: 'upload_book', args: { filename: 'wizards.pdf', data_base64: 'YWJj', title: 'Market Wizards' } },
    });
  });
});

describe('buildStopBookEvent / buildRetryBookEvent / buildDeleteBookEvent (2026-08-26)', () => {
  test('stop_book carries the book id', () => {
    expect(TradingPanel.buildStopBookEvent(7)).toEqual({
      type: 'call_tool', data: { name: 'stop_book', args: { book_id: 7 } },
    });
  });

  test('retry_book carries the book id', () => {
    expect(TradingPanel.buildRetryBookEvent(7)).toEqual({
      type: 'call_tool', data: { name: 'retry_book', args: { book_id: 7 } },
    });
  });

  test('resume_book carries the book id', () => {
    expect(TradingPanel.buildResumeBookEvent(7)).toEqual({
      type: 'call_tool', data: { name: 'resume_book', args: { book_id: 7 } },
    });
  });

  test('delete_book carries the book id', () => {
    expect(TradingPanel.buildDeleteBookEvent(7)).toEqual({
      type: 'call_tool', data: { name: 'delete_book', args: { book_id: 7 } },
    });
  });
});

describe('buildStartDiscoveryEvent / buildStopDiscoveryEvent (S31/#896)', () => {
  test('start with a duration includes duration_hours', () => {
    expect(TradingPanel.buildStartDiscoveryEvent(2.5)).toEqual({
      type: 'call_tool', data: { name: 'start_discovery', args: { duration_hours: 2.5 } },
    });
  });

  test('start with no duration omits duration_hours entirely (indefinite)', () => {
    expect(TradingPanel.buildStartDiscoveryEvent(null)).toEqual({
      type: 'call_tool', data: { name: 'start_discovery', args: {} },
    });
  });

  test('stop sends stop_discovery with no args', () => {
    expect(TradingPanel.buildStopDiscoveryEvent()).toEqual({
      type: 'call_tool', data: { name: 'stop_discovery', args: {} },
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

// S29 (#892), decisions #48-#51 -- Trading pane "Tickers" sub-tab.
// renderTickersUpdate calls mount.querySelectorAll/querySelector to wire
// canvas charts + hover, same as renderTradingUpdate above -- reuses this
// file's own fakeInteractiveMount() so those calls no-op instead of
// throwing; canvas drawing itself needs a real 2D context this suite
// doesn't have, so only the innerHTML string (stage text, badges, names)
// is asserted, matching this file's established style.

describe('initTickersView', () => {
  test('shows a loading placeholder in the tickers mount', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      global.document.getElementById = (id) => (id === 'trading-tickers-mount' ? mount : null);
      TradingPanel.initTickersView();
      expect(mount.innerHTML).toContain('Loading tickers');
    });
  });
});

describe('renderTickersUpdate', () => {
  test('no tickers renders the empty state', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTickersUpdate({ tickers: [] }, mount);
      expect(mount.innerHTML).toContain('No tickers in play yet');
    });
  });

  test('a screened ticker (no strategy yet) shows its own status, not a chart', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTickersUpdate({
        tickers: [{ symbol: 'NVDA', stage: 'screened', strategies: [] }],
      }, mount);
      expect(mount.innerHTML).toContain('NVDA');
      expect(mount.innerHTML).toContain('Screened');
      expect(mount.innerHTML).toContain('no strategy yet');
      expect(mount.innerHTML).not.toContain('trd-ticker-canvas');
    });
  });

  test('a rejected ticker (S30/#894) shows its gauntlet reason, not the generic screened text', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTickersUpdate({
        tickers: [{
          symbol: 'NVDA', stage: 'rejected', strategies: [],
          reason: 'vs_benchmark: underperformed by 3.2%',
        }],
      }, mount);
      expect(mount.innerHTML).toContain('NVDA');
      expect(mount.innerHTML).toContain('Rejected');
      expect(mount.innerHTML).toContain('vs_benchmark: underperformed by 3.2%');
      expect(mount.innerHTML).not.toContain('no strategy yet');
      expect(mount.innerHTML).not.toContain('trd-ticker-canvas');
    });
  });

  test('a validated strategy with zero fills shows "awaiting first paper trade", not a chart', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTickersUpdate({
        tickers: [{
          symbol: 'AAPL', stage: 'validated',
          strategies: [{ name: 'ma-cross', status: 'paper', segments: [] }],
        }],
      }, mount);
      expect(mount.innerHTML).toContain('ma-cross');
      expect(mount.innerHTML).toContain('>PAPER<');
      expect(mount.innerHTML).toContain('awaiting first paper trade');
      expect(mount.innerHTML).not.toContain('trd-ticker-canvas');
    });
  });

  test('a charting ticker renders one canvas per phase segment', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTickersUpdate({
        tickers: [{
          symbol: 'AAPL', stage: 'charting',
          strategies: [{
            name: 'ma-cross', status: 'live',
            segments: [
              { phase: 'paper', points: [{ ts: '2026-01-01T00:00:00', equity: 0, side: 'buy', pnl: 0, price: 100, strategy: 'ma-cross' }], benchmark: [] },
              { phase: 'live', points: [{ ts: '2026-01-05T00:00:00', equity: 2, side: 'buy', pnl: 2, price: 110, strategy: 'ma-cross' }], benchmark: [] },
            ],
          }],
        }],
      }, mount);
      expect(mount.innerHTML).toContain('trd-ticker-canvas-0-0-0');
      expect(mount.innerHTML).toContain('trd-ticker-canvas-0-0-1');
      expect(mount.innerHTML).toContain('PAPER vs. buy-and-hold');
      expect(mount.innerHTML).toContain('LIVE vs. buy-and-hold');
    });
  });

  test('multiple strategies on the same ticker all appear on one card', () => {
    withFakeDocument(() => {
      const mount = fakeInteractiveMount();
      TradingPanel.renderTickersUpdate({
        tickers: [{
          symbol: 'AAPL', stage: 'validated',
          strategies: [
            { name: 'strategy-one', status: 'paper', segments: [] },
            { name: 'strategy-two', status: 'paper', segments: [] },
          ],
        }],
      }, mount);
      expect(mount.innerHTML).toContain('strategy-one');
      expect(mount.innerHTML).toContain('strategy-two');
      // one card, not two -- both strategies nested under the single AAPL header
      expect((mount.innerHTML.match(/trd-ticker-card/g) || []).length).toBe(1);
    });
  });
});
