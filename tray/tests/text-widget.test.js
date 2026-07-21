'use strict';

// Tests for tray/lib/text-widget.js -- UI2 A4 (#484).
// Covers buildSaveMessage (pure, no DOM) which assembles the call_tool WS
// payload fired when the user clicks Save in a text widget.

const TextWidget = require('../lib/text-widget');

describe('buildSaveMessage', () => {
  test('assembles call_tool payload with tool name and content', () => {
    const msg = TextWidget.buildSaveMessage('doc_write', { doc_id: 1 }, 'hello world');
    expect(msg).toEqual({
      type: 'call_tool',
      data: { name: 'doc_write', args: { doc_id: 1, content: 'hello world' } },
    });
  });

  test('merges tool_args and content; content wins if key collides', () => {
    const msg = TextWidget.buildSaveMessage('doc_write', { doc_id: 5, content: 'old' }, 'new');
    expect(msg.data.args.content).toBe('new');
    expect(msg.data.args.doc_id).toBe(5);
  });

  test('special characters in content pass through unmodified', () => {
    const raw = '<b>&"test"</b>\nline2';
    const msg = TextWidget.buildSaveMessage('doc_write', { doc_id: 2 }, raw);
    expect(msg.data.args.content).toBe(raw);
  });

  test('empty tool_args results in only content in args', () => {
    const msg = TextWidget.buildSaveMessage('doc_write', {}, 'text');
    expect(msg.data.args).toEqual({ content: 'text' });
  });

  test('null tool_args treated as empty', () => {
    const msg = TextWidget.buildSaveMessage('doc_write', null, 'x');
    expect(msg.data.args).toEqual({ content: 'x' });
  });

  test('does not mutate the input toolArgs object', () => {
    const toolArgs = { doc_id: 5 };
    TextWidget.buildSaveMessage('doc_write', toolArgs, 'text');
    expect(toolArgs).toEqual({ doc_id: 5 });
  });

  test('empty tool string is preserved as-is', () => {
    const msg = TextWidget.buildSaveMessage('', { doc_id: 3 }, 'x');
    expect(msg.data.name).toBe('');
  });
});
