'use strict';

const ThinkingPanelMod = require('../lib/thinking-panel');

describe('ThinkingPanelMod', () => {
  test('formats tool_call turns correctly', () => {
    expect(ThinkingPanelMod.formatTurn({ kind: 'tool_call', tool_call: { name: 'search' } })).toBe('-> search');
  });

  test('formats tool_result turns correctly', () => {
    expect(ThinkingPanelMod.formatTurn({ kind: 'tool_result', tool_result: { result: 'ok' } })).toBe('<- ok');
  });

  test('ignores non-tool kinds', () => {
    expect(ThinkingPanelMod.formatTurn({ kind: 'user' })).toBe('');
    expect(ThinkingPanelMod.formatTurn({ kind: 'assistant' })).toBe('');
    expect(ThinkingPanelMod.formatTurn(null)).toBe('');
  });

  test('rowHtml renders a tool_call row with the right class and text', () => {
    const html = ThinkingPanelMod.rowHtml({ kind: 'tool_call', tool_call: { name: 'search' } });
    expect(html).toContain('class="thinking-row tool_call"');
    expect(html).toContain('-&gt; search');
  });

  test('rowHtml renders a tool_result row with the right class and text', () => {
    const html = ThinkingPanelMod.rowHtml({ kind: 'tool_result', tool_result: { result: 'ok' } });
    expect(html).toContain('class="thinking-row tool_result"');
    expect(html).toContain('&lt;- ok');
  });

  test('rowHtml returns empty string for a non-tool turn kind', () => {
    expect(ThinkingPanelMod.rowHtml({ kind: 'user', content: 'hello' })).toBe('');
  });

  test('rowHtml escapes HTML in tool_result text -- tool output can contain anything', () => {
    const html = ThinkingPanelMod.rowHtml({
      kind: 'tool_result',
      tool_result: { result: '<script>alert(1)</script>' },
    });
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  test('FEED_MAX is a positive integer the caller can cap the feed at', () => {
    expect(typeof ThinkingPanelMod.FEED_MAX).toBe('number');
    expect(ThinkingPanelMod.FEED_MAX).toBeGreaterThan(0);
  });
});
