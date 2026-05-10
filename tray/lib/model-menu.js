'use strict';

/**
 * Build the "Model" submenu for the tray context menu — Issue #29.
 *
 * Pure function so it can be unit-tested without spinning up Electron. Returns
 * a Menu template array (an array of entries that `Menu.buildFromTemplate`
 * accepts), which `tray/main.js` embeds under the top-level "Model" submenu.
 *
 * @param {object} opts
 * @param {Array<{id, label, is_cloud, is_active, is_last}>} opts.models
 * @param {string|null} opts.active
 * @param {string|null} opts.last
 * @param {boolean} opts.activeIsCloud
 * @param {Object<string,string>} opts.taskModels  task_type → model_id
 * @param {string[]} [opts.taskTypes]              task types to expose (default: ['chat','extraction'])
 * @param {(modelId: string) => void} opts.onSwitchModel
 * @param {(taskType: string, modelId: string|null) => void} opts.onSetTaskModel
 * @returns {Array<object>} Electron Menu template entries
 */
function buildModelSubmenu(opts) {
  const {
    models = [],
    active = null,
    last = null,
    activeIsCloud = false,
    taskModels = {},
    taskTypes = ['chat', 'extraction'],
    onSwitchModel = () => {},
    onSetTaskModel = () => {},
  } = opts || {};

  const cloudIndicator = activeIsCloud ? '☁ ' : '◉ ';
  const activeLabel = active ? `${cloudIndicator}Active: ${active}` : '◉ Active: —';
  const lastLabel = last && last !== active ? `Last used: ${last}` : null;

  const template = [
    { label: activeLabel, enabled: false },
  ];
  if (lastLabel) template.push({ label: lastLabel, enabled: false });
  template.push({ type: 'separator' });

  // Switch active model — radio selection across all known models
  for (const m of models) {
    template.push({
      label: formatModelLabel(m),
      type: 'radio',
      checked: !!m.is_active,
      click: () => onSwitchModel(m.id),
    });
  }

  // Per-task-type model mapping
  if (models.length > 0 && taskTypes.length > 0) {
    template.push({ type: 'separator' });
    for (const taskType of taskTypes) {
      const current = taskModels[taskType];
      template.push({
        label: `Task: ${taskType}${current ? ` → ${current}` : ' (use active)'}`,
        submenu: [
          {
            label: 'Use active model',
            type: 'radio',
            checked: !current,
            click: () => onSetTaskModel(taskType, null),
          },
          { type: 'separator' },
          ...models.map((m) => ({
            label: formatModelLabel(m),
            type: 'radio',
            checked: current === m.id,
            click: () => onSetTaskModel(taskType, m.id),
          })),
        ],
      });
    }
  }

  return template;
}

function formatModelLabel(m) {
  const cloud = m.is_cloud ? '☁ ' : '◉ ';
  const last = m.is_last && !m.is_active ? '  (last)' : '';
  return `${cloud}${m.label || m.id}${last}`;
}

module.exports = { buildModelSubmenu, formatModelLabel };
