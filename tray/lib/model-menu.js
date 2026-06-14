'use strict';

/**
 * Build the "Model" submenu for the tray context menu — Issue #29 + #37.
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
 * @param {() => void} [opts.onRefresh]            re-query Ollama for installed models (#37)
 * @returns {Array<object>} Electron Menu template entries
 */

// Dual-mode export: the same source feeds the Node test suite (and main.js)
// via require() AND can be loaded into a renderer via a plain <script src>
// tag (the windows run nodeIntegration:false per ADR-0007 so they can't
// require()). The body is wrapped in an IIFE so the module-private
// declarations (buildModelSubmenu, formatModelLabel, _exports) stay
// function-scoped. Classic <script src> tags share ONE global lexical
// environment, so a bare top-level `const _exports` here would collide with
// the identical declaration in any other dual-mode lib loaded into the same
// page -- a page-killing redeclaration SyntaxError that silently kills the
// whole renderer. require() scopes each module separately, which is why the
// Node test suite never catches this. Preventive hardening following #263/#264.
(function () {
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
    onRefresh = null,
  } = opts || {};

  const cloudIndicator = activeIsCloud ? '☁ ' : '◉ ';
  const activeLabel = active ? `${cloudIndicator}Active: ${active}` : '◉ Active: —';
  const lastLabel = last && last !== active ? `Last used: ${last}` : null;
  const hasLocal = models.some((m) => !m.is_cloud);

  const template = [
    { label: activeLabel, enabled: false },
  ];
  if (lastLabel) template.push({ label: lastLabel, enabled: false });
  if (!hasLocal) {
    template.push({ label: 'Ollama offline — local models unavailable', enabled: false });
  }
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

  // Refresh — re-query Ollama for newly-pulled models (issue #37). Always last
  // so it lives outside any radio group.
  if (onRefresh) {
    template.push({ type: 'separator' });
    template.push({ label: 'Refresh installed models', click: () => onRefresh() });
  }

  return template;
}

function formatModelLabel(m) {
  const cloud = m.is_cloud ? '☁ ' : '◉ ';
  const last = m.is_last && !m.is_active ? '  (last)' : '';
  return `${cloud}${m.label || m.id}${last}`;
}

// ── Dual-mode export ────────────────────────────────────────────────────
// Node (tests, main.js): module.exports = { ... }
// Browser (renderer):    window.ModelMenuMod = { ... }
const _exports = { buildModelSubmenu, formatModelLabel };

if (typeof module === 'object' && module && module.exports) {
  module.exports = _exports;
} else if (typeof window !== 'undefined') {
  window.ModelMenuMod = _exports;
}
})();
