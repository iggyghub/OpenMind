'use strict';

const { buildModelSubmenu, formatModelLabel } = require('../lib/model-menu');

const SAMPLE_MODELS = [
  { id: 'ollama/gemma4', label: 'Gemma 4 (local)', is_cloud: false, is_active: true,  is_last: false },
  { id: 'claude/haiku',  label: 'Claude Haiku',    is_cloud: true,  is_active: false, is_last: false },
];

// ── Active / last status header ──────────────────────────────────────────────

describe('header items', () => {
  test('first entry shows active model', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      activeIsCloud: false,
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    expect(tpl[0].label).toMatch(/Active: ollama\/gemma4/);
    expect(tpl[0].enabled).toBe(false);
  });

  test('cloud indicator appears when active is cloud', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'claude/haiku',
      activeIsCloud: true,
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    expect(tpl[0].label).toContain('☁');
  });

  test('local indicator appears when active is local', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      activeIsCloud: false,
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    expect(tpl[0].label).toContain('◉');
    expect(tpl[0].label).not.toContain('☁');
  });

  test('shows last used when different from active', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      last:   'claude/haiku',
      activeIsCloud: false,
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const labels = tpl.map((e) => e.label).filter(Boolean);
    expect(labels.some((l) => l && l.match(/Last used: claude\/haiku/))).toBe(true);
  });

  test('omits last-used row when same as active', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      last:   'ollama/gemma4',
      activeIsCloud: false,
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const labels = tpl.map((e) => e.label).filter(Boolean);
    expect(labels.some((l) => l && l.includes('Last used'))).toBe(false);
  });
});

// ── Model radio entries ──────────────────────────────────────────────────────

describe('model entries', () => {
  test('one radio entry per model', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const radios = tpl.filter((e) => e.type === 'radio');
    expect(radios).toHaveLength(SAMPLE_MODELS.length);
  });

  test('the active model entry is checked', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const radios = tpl.filter((e) => e.type === 'radio');
    expect(radios.find((e) => e.label.includes('Gemma 4')).checked).toBe(true);
    expect(radios.find((e) => e.label.includes('Haiku')).checked).toBe(false);
  });

  test('clicking a model fires onSwitchModel with its id', () => {
    let switched = null;
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      onSwitchModel: (id) => { switched = id; },
      onSetTaskModel: () => {},
    });
    const haiku = tpl.find((e) => e.label && e.label.includes('Haiku'));
    haiku.click();
    expect(switched).toBe('claude/haiku');
  });

  test('cloud models are visually marked', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const haiku = tpl.find((e) => e.label && e.label.includes('Haiku'));
    expect(haiku.label).toContain('☁');
  });

  test('non-active last-used model gets a (last) marker', () => {
    const models = [
      { id: 'a', label: 'A', is_cloud: false, is_active: true,  is_last: false },
      { id: 'b', label: 'B', is_cloud: false, is_active: false, is_last: true  },
    ];
    const tpl = buildModelSubmenu({
      models, active: 'a', last: 'b',
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const b = tpl.find((e) => e.label && e.label.startsWith('◉ B'));
    expect(b.label).toContain('(last)');
  });
});

// ── Per-task-type submenus ──────────────────────────────────────────────────

describe('per-task submenus', () => {
  test('one submenu per task type', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      taskTypes: ['chat', 'extraction'],
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const taskEntries = tpl.filter((e) => e.label && e.label.startsWith('Task:'));
    expect(taskEntries).toHaveLength(2);
  });

  test('task submenu lists "Use active" + every model', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      taskTypes: ['chat'],
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const chatEntry = tpl.find((e) => e.label && e.label.startsWith('Task: chat'));
    const radioCount = chatEntry.submenu.filter((e) => e.type === 'radio').length;
    expect(radioCount).toBe(1 + SAMPLE_MODELS.length);
  });

  test('"Use active" is checked when no mapping for that task', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      taskTypes: ['chat'],
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const chatEntry = tpl.find((e) => e.label && e.label.startsWith('Task: chat'));
    const useActive = chatEntry.submenu.find((e) => e.label === 'Use active model');
    expect(useActive.checked).toBe(true);
  });

  test('the mapped model is checked when task is pinned', () => {
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: { extraction: 'claude/haiku' },
      taskTypes: ['extraction'],
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    const extEntry = tpl.find((e) => e.label && e.label.startsWith('Task: extraction'));
    expect(extEntry.label).toContain('claude/haiku');
    const pinned = extEntry.submenu.find((e) => e.label && e.label.includes('Haiku'));
    expect(pinned.checked).toBe(true);
    const useActive = extEntry.submenu.find((e) => e.label === 'Use active model');
    expect(useActive.checked).toBe(false);
  });

  test('clicking a task-submenu model fires onSetTaskModel', () => {
    const calls = [];
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: {},
      taskTypes: ['extraction'],
      onSwitchModel: () => {},
      onSetTaskModel: (taskType, modelId) => calls.push([taskType, modelId]),
    });
    const extEntry = tpl.find((e) => e.label && e.label.startsWith('Task: extraction'));
    const haiku = extEntry.submenu.find((e) => e.label && e.label.includes('Haiku'));
    haiku.click();
    expect(calls).toEqual([['extraction', 'claude/haiku']]);
  });

  test('clicking "Use active" fires onSetTaskModel with null', () => {
    const calls = [];
    const tpl = buildModelSubmenu({
      models: SAMPLE_MODELS,
      active: 'ollama/gemma4',
      taskModels: { chat: 'claude/haiku' },
      taskTypes: ['chat'],
      onSwitchModel: () => {},
      onSetTaskModel: (taskType, modelId) => calls.push([taskType, modelId]),
    });
    const chatEntry = tpl.find((e) => e.label && e.label.startsWith('Task: chat'));
    const useActive = chatEntry.submenu.find((e) => e.label === 'Use active model');
    useActive.click();
    expect(calls).toEqual([['chat', null]]);
  });
});

// ── Empty / degenerate cases ────────────────────────────────────────────────

describe('edge cases', () => {
  test('no models still produces a header', () => {
    const tpl = buildModelSubmenu({
      models: [],
      active: null,
      taskModels: {},
      onSwitchModel: () => {},
      onSetTaskModel: () => {},
    });
    expect(tpl[0].label).toMatch(/Active: —/);
    // No radios, no task submenus
    expect(tpl.filter((e) => e.type === 'radio')).toHaveLength(0);
    expect(tpl.filter((e) => e.label && e.label.startsWith('Task:'))).toHaveLength(0);
  });

  test('formatModelLabel handles missing label', () => {
    expect(formatModelLabel({ id: 'x', is_cloud: false })).toContain('x');
  });
});
