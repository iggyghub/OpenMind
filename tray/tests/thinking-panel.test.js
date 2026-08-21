'use strict';

const ThinkingPanel = require('../../lib/thinking-panel');

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
    ThinkingPanel.init(container, null);
    expect(container.appendChild).toHaveBeenCalledTimes(1);
    const feed = container.appendChild.mock.calls[0][0];
    expect(feed.className).toBe('thinking-feed');
    expect(feed.style.display).toBe('flex');
    expect(feed.style.flexDirection).toBe('column');
    expect(feed.style.overflowY).toBe('auto');
  });

  test('WS listener appends lines for tool events and auto-scrolls', () => {
    const container = { innerHTML: '', appendChild: jest.fn() };
    const ws = { addEventListener: jest.fn() };
    ThinkingPanel.init(container, ws);
    expect(ws.addEventListener).toHaveBeenCalledWith('message', expect.any(Function));
    
    const handler = ws.addEventListener.mock.calls[0][1];
    
    // Emit a tool_call
    handler({ data: JSON.stringify({ type: 'conversation_turn_emitted', turn: { kind: 'tool_call', tool_call: { name: 'ls' } } }) });
    let feed = container.appendChild.mock.calls[0][0];
    expect(feed.children.length).toBe(1);
    expect(feed.children[0].textContent).toBe('-> ls');
    expect(feed.scrollTop).toBe(0); // first scroll
    
    // Emit a tool_result
    handler({ data: JSON.stringify({ type: 'conversation_turn_emitted', turn: { kind: 'tool_result', tool_result: { result: 'success' } } }) });
    expect(feed.children.length).toBe(2);
    expect(feed.children[1].textContent).toBe('<- success');
    
    // Emit non-tool (should be ignored)
    handler({ data: JSON.stringify({ type: 'conversation_turn_emitted', turn: { kind: 'user', content: 'hello' } }) });
    expect(feed.children.length).toBe(2); // count unchanged
  });
});
