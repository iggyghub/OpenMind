'use strict';

const ThinkingPanelMod = require('../lib/thinking-panel');

// Real conversation_turn_emitted wire shape: { kind, content, ts }.
// content.result exists on tool_result thanks to chain_engine.py recording
// it (#789) -- see cerebral/db/conversation.py's to_dict().

describe('ThinkingPanelMod', () => {
  describe('humanizeToolName', () => {
    test('converts snake_case to Title Case', () => {
      expect(ThinkingPanelMod.humanizeToolName('gmail_search')).toBe('Gmail Search');
    });

    test('falls back to "Tool" for a missing name', () => {
      expect(ThinkingPanelMod.humanizeToolName(undefined)).toBe('Tool');
      expect(ThinkingPanelMod.humanizeToolName('')).toBe('Tool');
    });

    test('handles a single-word tool name', () => {
      expect(ThinkingPanelMod.humanizeToolName('search')).toBe('Search');
    });
  });

  describe('groupHeaderHtml', () => {
    test('renders a header for a user_text turn', () => {
      const html = ThinkingPanelMod.groupHeaderHtml({ kind: 'user_text', content: { text: 'find that invoice' } });
      expect(html).toContain('class="thinking-group-header"');
      expect(html).toContain('find that invoice');
    });

    test('renders a header for a user_voice turn', () => {
      const html = ThinkingPanelMod.groupHeaderHtml({ kind: 'user_voice', content: { text: 'what time is it' } });
      expect(html).toContain('what time is it');
    });

    test('falls back to a placeholder for an empty voice transcript', () => {
      const html = ThinkingPanelMod.groupHeaderHtml({ kind: 'user_voice', content: { text: '' } });
      expect(html).toContain('(voice message)');
    });

    test('truncates a long message', () => {
      const long = 'x'.repeat(200);
      const html = ThinkingPanelMod.groupHeaderHtml({ kind: 'user_text', content: { text: long } });
      expect(html).toContain('…');
      expect(html.length).toBeLessThan(long.length + 60);
    });

    test('returns empty string for a non-user turn kind', () => {
      expect(ThinkingPanelMod.groupHeaderHtml({ kind: 'tool_call', content: { name: 'search' } })).toBe('');
    });

    test('escapes HTML in the user message', () => {
      const html = ThinkingPanelMod.groupHeaderHtml({ kind: 'user_text', content: { text: '<script>alert(1)</script>' } });
      expect(html).not.toContain('<script>');
      expect(html).toContain('&lt;script&gt;');
    });
  });

  describe('stepRowHtml', () => {
    test('renders a tool_call row using content.name (real wire shape)', () => {
      const html = ThinkingPanelMod.stepRowHtml({ kind: 'tool_call', content: { name: 'gmail_search', args: { query: 'invoice' } } });
      expect(html).toContain('class="thinking-row tool_call is-pending"');
      expect(html).toContain('Gmail Search');
    });

    test('a tool_call row is marked is-pending -- a long-running tool (e.g. self_dev_campaign) shows a working spinner until its result arrives', () => {
      const html = ThinkingPanelMod.stepRowHtml({ kind: 'tool_call', content: { name: 'self_dev_campaign' } });
      expect(html).toContain('is-pending');
    });

    test('renders a tool_result row using content.result (real wire shape)', () => {
      const html = ThinkingPanelMod.stepRowHtml({ kind: 'tool_result', content: { name: 'gmail_search', is_error: false, result: 'Found 3 emails' } });
      expect(html).toContain('class="thinking-row tool_result"');
      expect(html).toContain('Found 3 emails');
    });

    test('marks a failed tool_result with is-error and an Error: prefix', () => {
      const html = ThinkingPanelMod.stepRowHtml({ kind: 'tool_result', content: { name: 'gmail_search', is_error: true, result: 'auth expired' } });
      expect(html).toContain('is-error');
      expect(html).toContain('Error: auth expired');
    });

    test('returns empty string for a non-tool turn kind', () => {
      expect(ThinkingPanelMod.stepRowHtml({ kind: 'user_text', content: { text: 'hi' } })).toBe('');
    });

    test('escapes HTML in tool_result text -- tool output can contain anything', () => {
      const html = ThinkingPanelMod.stepRowHtml({
        kind: 'tool_result',
        content: { name: 'web_fetch', result: '<script>alert(1)</script>' },
      });
      expect(html).not.toContain('<script>');
      expect(html).toContain('&lt;script&gt;');
    });
  });

  describe('rowHtml', () => {
    test('a user turn renders as a group header, not a step row', () => {
      const html = ThinkingPanelMod.rowHtml({ kind: 'user_text', content: { text: 'find that invoice' } });
      expect(html).toContain('thinking-group-header');
    });

    test('a tool_call turn renders as a step row', () => {
      const html = ThinkingPanelMod.rowHtml({ kind: 'tool_call', content: { name: 'gmail_search' } });
      expect(html).toContain('thinking-row tool_call');
    });

    test('returns empty string for an ignored turn kind (e.g. felix_speech)', () => {
      expect(ThinkingPanelMod.rowHtml({ kind: 'felix_speech', content: { text: 'done' } })).toBe('');
    });
  });

  test('FEED_MAX is a positive integer the caller can cap the feed at', () => {
    expect(typeof ThinkingPanelMod.FEED_MAX).toBe('number');
    expect(ThinkingPanelMod.FEED_MAX).toBeGreaterThan(0);
  });
});
