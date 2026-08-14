'use strict';

// Tests for tray/lib/action-widget.js -- S5 #542 (Skills panel).
// Covers buildActionMessage (pure, no DOM) which assembles the call_tool WS
// payload fired when the user clicks an action button (install / toggle /
// uninstall).

const ActionWidget = require('../lib/action-widget');

describe('buildActionMessage', () => {
  test('assembles call_tool payload with fixed tool_args, no input_arg', () => {
    const msg = ActionWidget.buildActionMessage('skill_uninstall', { name: 'grill-me' }, '', '');
    expect(msg).toEqual({
      type: 'call_tool',
      data: { name: 'skill_uninstall', args: { name: 'grill-me' } },
    });
  });

  test('merges the input value under input_arg', () => {
    const msg = ActionWidget.buildActionMessage('skill_install', {}, 'repo', 'owner/repo');
    expect(msg.data.args).toEqual({ repo: 'owner/repo' });
  });

  test('input value wins if tool_args already has that key', () => {
    const msg = ActionWidget.buildActionMessage('skill_install', { repo: 'old' }, 'repo', 'new');
    expect(msg.data.args.repo).toBe('new');
  });

  test('empty tool_args with no input_arg results in empty args', () => {
    const msg = ActionWidget.buildActionMessage('skill_enable', {}, '', '');
    expect(msg.data.args).toEqual({});
  });

  test('null tool_args treated as empty', () => {
    const msg = ActionWidget.buildActionMessage('skill_enable', null, 'name', 'x');
    expect(msg.data.args).toEqual({ name: 'x' });
  });

  test('does not mutate the input toolArgs object', () => {
    const toolArgs = { name: 'x' };
    ActionWidget.buildActionMessage('skill_disable', toolArgs, '', '');
    expect(toolArgs).toEqual({ name: 'x' });
  });

  test('empty tool string is preserved as-is', () => {
    const msg = ActionWidget.buildActionMessage('', { name: 'x' }, '', '');
    expect(msg.data.name).toBe('');
  });

  // Second optional field (#686): url + category on one action (batch-start).
  test('merges a second field under input_arg2', () => {
    const msg = ActionWidget.buildActionMessage(
      'video_batch_start', {}, 'url', 'https://yt/@indydevdan', 'category', 'harness improvement'
    );
    expect(msg.data.args).toEqual({
      url: 'https://yt/@indydevdan',
      category: 'harness improvement',
    });
  });

  test('omits the second field when input_arg2 is empty (backward-compatible)', () => {
    const msg = ActionWidget.buildActionMessage('t', {}, 'url', 'u', '', '');
    expect(msg.data.args).toEqual({ url: 'u' });
  });

  // Boolean checkbox field (#688): verify toggle on batch-start.
  test('sends the checkbox arg as a real boolean (checked)', () => {
    const msg = ActionWidget.buildActionMessage(
      'video_batch_start', {}, 'url', 'u', 'category', 'harness improvement', 'verify', true
    );
    expect(msg.data.args).toEqual({ url: 'u', category: 'harness improvement', verify: true });
  });

  test('unchecked checkbox sends false, not a string', () => {
    const msg = ActionWidget.buildActionMessage('t', {}, 'url', 'u', '', '', 'verify', false);
    expect(msg.data.args.verify).toBe(false);
  });

  test('omits the checkbox when checkbox_arg is empty (backward-compatible)', () => {
    const msg = ActionWidget.buildActionMessage('t', {}, 'url', 'u', '', '', '', false);
    expect(msg.data.args).toEqual({ url: 'u' });
  });
});

// initActionWidgets itself is DOM-wiring glue (querySelectorAll/addEventListener)
// -- untested at unit level here, same as text-widget.js's initTextWidgets;
// exercised via the real renderer instead. Kept to a guard against a null/
// undefined container, mirroring the early-return checks in the source.
describe('initActionWidgets guards', () => {
  test('does not throw on a container without querySelectorAll', () => {
    expect(() => ActionWidget.initActionWidgets(null, () => {})).not.toThrow();
    expect(() => ActionWidget.initActionWidgets(undefined, () => {})).not.toThrow();
    expect(() => ActionWidget.initActionWidgets({}, () => {})).not.toThrow();
  });
});

describe('initToggleWidgets guards', () => {
  test('does not throw on a container without querySelectorAll', () => {
    expect(() => ActionWidget.initToggleWidgets(null, () => {})).not.toThrow();
    expect(() => ActionWidget.initToggleWidgets(undefined, () => {})).not.toThrow();
    expect(() => ActionWidget.initToggleWidgets({}, () => {})).not.toThrow();
  });
});

describe('buildMoveMessage', () => {
  test('assembles a video_move_cluster call with numeric cluster_id', () => {
    const msg = ActionWidget.buildMoveMessage('video_move_cluster', '1238', 'harness improvements');
    expect(msg).toEqual({
      type: 'call_tool',
      data: { name: 'video_move_cluster', args: { cluster_id: 1238, collection: 'harness improvements' } },
    });
  });

  test('defaults the tool name and coerces id', () => {
    const msg = ActionWidget.buildMoveMessage('', 7, 'money-making idea');
    expect(msg.data.name).toBe('video_move_cluster');
    expect(msg.data.args.cluster_id).toBe(7);
  });
});

describe('initMoveWidgets', () => {
  test('does not throw on missing / bad containers', () => {
    expect(() => ActionWidget.initMoveWidgets(null, () => {})).not.toThrow();
    expect(() => ActionWidget.initMoveWidgets({}, () => {})).not.toThrow();
  });
});
