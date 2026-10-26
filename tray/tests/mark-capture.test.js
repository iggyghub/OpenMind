'use strict';

// tray/lib/mark-capture.js + tray/lib/mark-kit-host.js -- the two halves of a
// mark-kit screenshot (renderer request -> Cerebral relay -> tray main capture).

const { sanitizeRect, captureMark } = require('../lib/mark-capture');
const MarkKitHost = require('../lib/mark-kit-host');

test('sanitizeRect rounds good rects and refuses junk', () => {
  expect(sanitizeRect({ x: 1.4, y: 2, width: 30.6, height: 40 })).toEqual({ x: 1, y: 2, width: 31, height: 40 });
  expect(sanitizeRect({ x: -1, y: 0, width: 5, height: 5 })).toBeNull();
  expect(sanitizeRect({ x: 0, y: 0, width: 0, height: 5 })).toBeNull();
  expect(sanitizeRect({ x: 'a', y: 0, width: 5, height: 5 })).toBeNull();
  expect(sanitizeRect(null)).toBeNull();
});

test('captureMark returns the data URL, or null when capture fails', async () => {
  const img = { isEmpty: () => false, toDataURL: () => 'data:image/png;base64,AAA' };
  const wc = { capturePage: jest.fn(async () => img) };
  await expect(captureMark(wc, { x: 0, y: 0, width: 10, height: 10 })).resolves.toBe('data:image/png;base64,AAA');
  expect(wc.capturePage).toHaveBeenCalledWith({ x: 0, y: 0, width: 10, height: 10 });
  await expect(captureMark({ capturePage: async () => { throw new Error('gone'); } }, { x: 0, y: 0, width: 1, height: 1 })).resolves.toBeNull();
  await expect(captureMark(wc, { x: 0 })).resolves.toBeNull();
});

test('host capture round-trips through send + onEvent, and gives null when offline', async () => {
  const sent = [];
  const host = MarkKitHost.create((ev) => { sent.push(ev); return true; });
  const p = host.capture({ x: 0, y: 0, width: 5, height: 5 });
  expect(sent[0].type).toBe('mark_capture');
  host.onEvent({ type: 'mark_capture_result', data: { id: sent[0].data.id, png: 'data:x' } });
  await expect(p).resolves.toBe('data:x');

  const offline = MarkKitHost.create(() => false);
  await expect(offline.capture({ x: 0, y: 0, width: 5, height: 5 })).resolves.toBeNull();
});

test('host store calls the gated marks tools and answers FIFO per tool name', async () => {
  const sent = [];
  const host = MarkKitHost.create((ev) => { sent.push(ev); return true; });
  const a = host.store.add({ id: 'm1', note: 'n', shot: 'data:png', stored: false });
  const b = host.store.list();
  expect(sent[0]).toEqual({ type: 'call_tool', data: { name: 'marks_add', args: { mark: { id: 'm1', note: 'n' }, shot: 'data:png' }, record: false } });
  expect(sent[1].data.name).toBe('marks_list');
  host.onEvent({ type: 'tool_result', data: { name: 'marks_list', content: '{"marks":[{"id":"x"}]}', is_error: false } });
  host.onEvent({ type: 'tool_result', data: { name: 'marks_add', content: 'Denied', is_error: true } });
  await expect(b).resolves.toEqual([{ id: 'x' }]);
  await expect(a).resolves.toEqual({ error: 'Denied' });  // no id: the core keeps it buffered
});

test('shotUrl turns a stored Windows path into a file URL', () => {
  const host = MarkKitHost.create(() => true);
  expect(host.shotUrl({ shot_abs: String.raw`C:\OpenMind\.marks\shots\m 1.png` })).toBe('file:///C:/OpenMind/.marks/shots/m%201.png');
  expect(host.shotUrl({})).toBeNull();
});

test('fix: models from models_list with tiers, coding model preferred, run calls marks_fix', async () => {
  const sent = [];
  const host = MarkKitHost.create((ev) => { sent.push(ev); return true; });
  host.onEvent({ type: 'models_list', data: { active: 'custom/budd', task_models: { coding: 'claude/sonnet' }, models: [
    { id: 'custom/budd', label: 'Budd', is_custom: true }, { id: 'claude/sonnet', label: 'Sonnet', is_cloud: true },
    { id: 'ollama/qwen3:8b', label: 'Qwen' }, { id: 'off', label: 'Off', enabled: false }] } });
  await expect(host.fix.models()).resolves.toEqual([
    { id: 'custom/budd', label: 'Budd', where: 'remote' }, { id: 'claude/sonnet', label: 'Sonnet', where: 'cloud' },
    { id: 'ollama/qwen3:8b', label: 'Qwen', where: 'local' }]);
  expect(host.fix.preferred()).toBe('claude/sonnet');
  const r = host.fix.run('custom/budd');
  expect(sent[0].data).toEqual({ name: 'marks_fix', args: { model: 'custom/budd' }, record: false });
  host.onEvent({ type: 'tool_result', data: { name: 'marks_fix', content: 'No open marks to fix.', is_error: true } });
  await expect(r).resolves.toEqual({ error: 'No open marks to fix.' });
  const reload = jest.fn();
  host.setKit({ reload });
  host.onEvent({ type: 'marks_changed', data: {} });
  expect(reload).toHaveBeenCalled();
});
