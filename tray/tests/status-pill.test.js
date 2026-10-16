const { pillView } = require('../lib/status-pill');
const labels = { passive: 'Passive', active: 'Active', thinking: 'Thinking', speaking: 'Speaking' };
const humanize = require('../lib/thinking-panel').humanizeToolName;

test('shows base state when nothing is working', () => {
  expect(pillView('passive', { count: 0, tool: null }, labels, humanize)).toEqual({ state: 'passive', label: 'Passive' });
});

test('shows Working with the tool name', () => {
  expect(pillView('active', { count: 1, tool: 'rea_analyze_javascript' }, labels, humanize)).toEqual({ state: 'working', label: 'Working: Rea Analyze Javascript' });
});

test('adds a plus count for more than one tool', () => {
  expect(pillView('active', { count: 2, tool: 'rea_analyze_javascript' }, labels, humanize)).toEqual({ state: 'working', label: 'Working: Rea Analyze Javascript (+1)' });
});

test('thinking wins over working', () => {
  expect(pillView('thinking', { count: 3, tool: 'rea_analyze_javascript' }, labels, humanize)).toEqual({ state: 'thinking', label: 'Thinking' });
});

test('speaking wins over working', () => {
  expect(pillView('speaking', { count: 3, tool: 'rea_analyze_javascript' }, labels, humanize)).toEqual({ state: 'speaking', label: 'Speaking' });
});
