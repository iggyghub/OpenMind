'use strict';

const ThinkingPanel = require('../lib/thinking-panel');

describe('ThinkingPanel', () => {
  test('formats tool_call turns correctly', () => {
    expect(ThinkingPanel.formatTurn({ kind: 'tool_call', tool_call: { name: 'search' } })).toBe('-> search');
  });

  test('formats tool_result turns correctly', () => {
    expect(ThinkingPanel.formatTurn({ kind: 'tool_result', tool_result: { result: 'ok' } })).toBe('<- ok');
  });

  test('ignores non-tool kinds', () => {
    expect(ThinkingPanel.formatTurn({ kind: 'user' })).toBe('');
    expect(ThinkingPanel.formatTurn({ kind: 'assistant' })).toBe('');
    expect(ThinkingPanel.formatTurn(null)).toBe('');
  });

  test('init creates feed container with correct structure', () => {
    const container = { innerHTML: '', appendChild: jest.fn() };
    ThinkingPanel.init(container);
    expect(container.appendChild).toHaveBeenCalledTimes(1);
    const feed = container.appendChild.mock.calls[0][0];
    expect(feed.className).toBe('thinking-feed');
    expect(feed.style.display).toBe('flex');
    expect(feed.style.flexDirection).toBe('column');
    expect(feed.style.overflowY).toBe('auto');
  });

  test('appendTurn adds formatted turns to the feed and auto-scrolls', () => {
    const container = { innerHTML: '', appendChild: jest.fn(), querySelector: jest.fn(() => ({ children: [], appendChild: jest.fn(), scrollTop: 0 })) };
    ThinkingPanel.init(container);
    ThinkingPanel.appendTurn(container, { kind: 'tool_call', tool_call: { name: 'ls' } });
    const feed = container.querySelector('.thinking-feed');
    expect(feed.appendChild).toHaveBeenCalledTimes(1);
    expect(feed.appendChild.mock.calls[0][0].textContent).toBe('-> ls');
    expect(feed.scrollTop).toBe(0);
    
    // Emit a tool_result
    ThinkingPanel.appendTurn(container, { kind: 'tool_result', tool_result: { result: 'success' } });
    expect(feed.appendChild).toHaveBeenCalledTimes(2);
    expect(feed.appendChild.mock.calls[1][0].textContent).toBe('<- success');
    
    // Emit non-tool (should be ignored)
    ThinkingPanel.appendTurn(container, { kind: 'user', content: 'hello' });
    expect(feed.appendChild).toHaveBeenCalledTimes(2); // count unchanged
  });
});
