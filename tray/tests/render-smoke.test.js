'use strict';

// Headless render check for tray/windows/main.html (UI overhaul S1, issue #284).
//
// Reads the HTML, asserts that every SMOKE_ROUTES pane element and nav item
// exists, verifies the inline script parses without syntax errors, and writes
// a serialised-DOM artifact to .claude/tmp/render-smoke/.
//
// Each later slice appends its newly-introduced routes to SMOKE_ROUTES and
// adds any element-level assertions it needs.

const fs   = require('fs');
const path = require('path');
const vm   = require('vm');
const { parse } = require('node-html-parser');

const HTML_PATH    = path.resolve(__dirname, '../windows/main.html');
const ARTIFACT_DIR = path.resolve(__dirname, '../../.claude/tmp/render-smoke');

// S5 #473: route collapse 16->4.
// PANE_ROUTES: all routes that must have a pane element in the DOM (old ones
//   kept as shells or content-moved; new ones added).
// NAV_ROUTES: only the 4 top-level nav sections (profiles removed from nav).
const PANE_ROUTES = [
  // Original 16 routes (kept as shells or with content)
  'conversation', 'quick-ask', 'queue', 'insights', 'memory',
  'permissions', 'credentials', 'profiles', 'settings',
  // 'models' pane removed: model settings folded into the Settings pane so the
  // 4-section nav can reach them (the #models hash still redirects to settings).
  'conversations', 'integrations', 'recipes', 'job-search', 'documents',
  // S5 new routes
  'harness', 'library',
];
const NAV_ROUTES = ['conversation', 'harness', 'library', 'settings'];

// Legacy alias kept for tests that loop over "routes that need panes".
const SMOKE_ROUTES = PANE_ROUTES;

let root;
let inlineScript;

beforeAll(() => {
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  root = parse(html);

  // Extract the last inline <script> block (the main renderer body, not the
  // <script src=...> lib tags).
  const allScripts = root.querySelectorAll('script');
  const inlineScripts = allScripts.filter(s => !s.getAttribute('src'));
  expect(inlineScripts.length).toBeGreaterThan(0);
  inlineScript = inlineScripts[inlineScripts.length - 1].text;

  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
});

// ── Pane elements ────────────────────────────────────────────────────────────

test('every expected pane element exists', () => {
  for (const route of PANE_ROUTES) {
    const pane = root.querySelector(`.pane[data-route="${route}"]`);
    expect(pane).not.toBeNull();
  }
});

// ── Nav items (S5: 4 sections only) ──────────────────────────────────────────

test('nav has exactly 4 top-level sections (S5)', () => {
  const navItems = root.querySelectorAll('.nav-item');
  expect(navItems.length).toBe(4);
});

test('every expected nav item exists (S5: 4 sections)', () => {
  for (const route of NAV_ROUTES) {
    const nav = root.querySelector(`.nav-item[data-route="${route}"]`);
    expect(nav).not.toBeNull();
  }
});

test('profiles is NOT a nav item (S5: moved to header switcher)', () => {
  const profilesNav = root.querySelector('.nav-item[data-route="profiles"]');
  expect(profilesNav).toBeNull();
});

// ── S5 -- profile switcher in header ─────────────────────────────────────────

test('header has profile switcher button and dropdown (S5)', () => {
  const btn      = root.querySelector('#prof-switcher-btn');
  const dropdown = root.querySelector('#prof-switcher-dropdown');
  const nameEl   = root.querySelector('#prof-switcher-name');
  expect(btn).not.toBeNull();
  expect(dropdown).not.toBeNull();
  expect(nameEl).not.toBeNull();
  // Dropdown must be hidden by default.
  expect(dropdown.getAttribute('hidden')).not.toBeNull();
  // Must live in the header, not inside any pane.
  let el = btn.parentNode;
  let insideHeader = false;
  while (el) {
    const cls = el.classNames || '';
    if (cls.split(/\s+/).includes('header')) insideHeader = true;
    el = el.parentNode;
  }
  expect(insideHeader).toBe(true);
});

// ── S5 -- library pane ────────────────────────────────────────────────────────

test('library pane exists with sub-tab bar (S5)', () => {
  const pane = root.querySelector('.pane[data-route="library"]');
  expect(pane).not.toBeNull();
  const tabs = pane.querySelector('#lib-tabs');
  expect(tabs).not.toBeNull();
  // Seven sub-tabs (memory / insights / recipes / documents / job-search / videos / github).
  const tabBtns = pane.querySelectorAll('.lib-tab');
  expect(tabBtns.length).toBe(7);
});

test('library pane contains memory, insights, recipes, documents, job-search, videos, github sub-sections', () => {
  const pane = root.querySelector('.pane[data-route="library"]');
  expect(pane).not.toBeNull();
  for (const sub of ['memory', 'insights', 'recipes', 'documents', 'job-search', 'videos', 'github']) {
    const el = pane.querySelector(`.lib-sub[data-lib="${sub}"]`);
    expect(el).not.toBeNull();
  }
});

// ── S5 -- harness pane (renamed from plugins) ─────────────────────────────────

test('harness pane exists (renamed from plugins) (S5)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  expect(pane).not.toBeNull();
});

// ── S3 — section-collapse lib present ────────────────────────────────────────

test('section-collapse.js script tag is present (S3)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('section-collapse'));
  expect(found).toBe(true);
});

// ── S4 — federated search shell present ──────────────────────────────────────

test('search-registry.js script tag is present (S4)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('search-registry'));
  expect(found).toBe(true);
});

test('header search input + elsewhere panel exist (S4)', () => {
  const input = root.querySelector('#hdr-search-input');
  expect(input).not.toBeNull();
  expect(input.getAttribute('type')).toBe('search');

  const elsewherePanel = root.querySelector('#hdr-search-elsewhere');
  expect(elsewherePanel).not.toBeNull();

  const elsewhereList = root.querySelector('#hdr-search-elsewhere-list');
  expect(elsewhereList).not.toBeNull();
});

test('search bar is inside the static header so it persists across panes (S4)', () => {
  const input = root.querySelector('#hdr-search-input');
  expect(input).not.toBeNull();
  // Walk ancestors: it must sit inside .header, not inside any .pane.
  let el = input.parentNode;
  let insideHeader = false;
  let insidePane   = false;
  while (el) {
    const cls = el.classNames || el.classList || '';
    const has = (name) => (typeof cls === 'string'
                            ? cls.split(/\s+/).includes(name)
                            : (cls.contains && cls.contains(name)));
    if (has('header')) insideHeader = true;
    if (has('pane'))   insidePane = true;
    el = el.parentNode;
  }
  expect(insideHeader).toBe(true);
  expect(insidePane).toBe(false);
});

// ── Model settings live in the Settings pane (reachable from the 4-section nav) ──

test('settings pane contains model control elements', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#set-active-header')).not.toBeNull();
  expect(pane.querySelector('#set-model-priority-list')).not.toBeNull();
  expect(pane.querySelector('#set-model-fallback-toggle')).not.toBeNull();
  expect(pane.querySelector('#set-task-list')).not.toBeNull();
  expect(pane.querySelector('#set-refresh-btn')).not.toBeNull();
  expect(pane.querySelector('#set-local-only-toggle')).not.toBeNull();
});

test('standalone models pane no longer exists', () => {
  expect(root.querySelector('.pane[data-route="models"]')).toBeNull();
});

test('settings pane has General, AI-models, Sign-in + Permissions sub-tabs', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  const tabs = pane.querySelectorAll('.set-tab');
  expect(tabs.length).toBe(4);
  const subs = Array.from(tabs).map((t) => t.getAttribute('data-set-sub'));
  expect(subs).toContain('general');
  expect(subs).toContain('models');
  expect(subs).toContain('signin');
  expect(subs).toContain('permissions');
  // Model controls live in the models sub-pane; appearance in the general one.
  expect(pane.querySelector('.set-subpane[data-set-sub="models"] #set-model-priority-list')).not.toBeNull();
  expect(pane.querySelector('.set-subpane[data-set-sub="general"] #set-scale-select')).not.toBeNull();
  // Sign-in sub-tab absorbs the channels list (Discord/…) and Google card;
  // Permissions sub-tab hosts the capability/tool tier rows.
  expect(pane.querySelector('.set-subpane[data-set-sub="signin"] #int-channels-list')).not.toBeNull();
  expect(pane.querySelector('.set-subpane[data-set-sub="signin"] #cred-card-google')).not.toBeNull();
  // Dedicated Discord user-token card (writes to discord_user/api_token).
  expect(pane.querySelector('.set-subpane[data-set-sub="signin"] #cred-card-discord')).not.toBeNull();
  expect(pane.querySelector('.set-subpane[data-set-sub="signin"] #cred-discord-token')).not.toBeNull();
  expect(pane.querySelector('.set-subpane[data-set-sub="permissions"] #perm-capability-rows')).not.toBeNull();
});

test('model priority panel replaces switch-model list (P2 #532)', () => {
  // Old single-select switch gone; priority panel present.
  expect(inlineScript).not.toContain('__none__');
  expect(inlineScript).toMatch(/set-model-priority-list/);
  expect(inlineScript).toMatch(/set-model-fallback-toggle/);
});

test('models pane task cards include tool + quality task types (#349)', () => {
  const m = inlineScript.match(/SET_TASK_TYPES\s*=\s*\[([^\]]*)\]/);
  expect(m).not.toBeNull();
  expect(m[1]).toContain("'tool'");
  expect(m[1]).toContain("'quality'");
});

test('settings pane also keeps its appearance/voice controls alongside models', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  // Model controls and app settings coexist in one Settings pane now.
  expect(pane.querySelector('#set-model-priority-list')).not.toBeNull();
  expect(pane.querySelector('#set-scale-select')).not.toBeNull();
});

// ── S6 — appearance controls in settings pane ────────────────────────────────

test('settings pane contains appearance controls (S6)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#set-scale-select')).not.toBeNull();
  expect(pane.querySelector('#set-theme-chips')).not.toBeNull();
  expect(pane.querySelector('#set-accent-picker')).not.toBeNull();
  // Three theme preset buttons must be present.
  const chips = pane.querySelectorAll('.set-theme-chip');
  expect(chips.length).toBeGreaterThanOrEqual(3);
});

// ── S7 — mic-mode control in static header ───────────────────────────────────

test('mic-mode control exists in static header (S7)', () => {
  const ctrl = root.querySelector('#hdr-mic-mode');
  expect(ctrl).not.toBeNull();

  // Must be inside .header, not inside any .pane.
  let el = ctrl.parentNode;
  let insideHeader = false;
  let insidePane   = false;
  while (el) {
    const cls = el.classNames || '';
    const has = (name) => cls.split(/\s+/).includes(name);
    if (has('header')) insideHeader = true;
    if (has('pane'))   insidePane   = true;
    el = el.parentNode;
  }
  expect(insideHeader).toBe(true);
  expect(insidePane).toBe(false);

  // Three segment buttons with correct data-mic values.
  const segs = ctrl.querySelectorAll('.hdr-mic-seg[data-mic]');
  expect(segs.length).toBe(3);
  const vals = segs.map(s => s.getAttribute('data-mic'));
  expect(vals).toContain('passive');
  expect(vals).toContain('ptt');
  expect(vals).toContain('disabled');
});

test('settings pane contains mic-mode select (S7)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#set-mic-mode-select')).not.toBeNull();
  const opts = pane.querySelectorAll('#set-mic-mode-select option');
  expect(opts.length).toBe(3);
});

// ── S8 — TTS controls ────────────────────────────────────────────────────────

test('TTS mute button and volume slider exist in static header (S8)', () => {
  const muteBtn = root.querySelector('#hdr-tts-mute');
  const volSlider = root.querySelector('#hdr-tts-vol');
  expect(muteBtn).not.toBeNull();
  expect(volSlider).not.toBeNull();

  // Both must be inside .header, not inside any .pane.
  for (const el of [muteBtn, volSlider]) {
    let node = el.parentNode;
    let insideHeader = false;
    let insidePane   = false;
    while (node) {
      const cls = node.classNames || '';
      const has = (name) => cls.split(/\s+/).includes(name);
      if (has('header')) insideHeader = true;
      if (has('pane'))   insidePane   = true;
      node = node.parentNode;
    }
    expect(insideHeader).toBe(true);
    expect(insidePane).toBe(false);
  }

  // Volume slider attributes.
  expect(volSlider.getAttribute('type')).toBe('range');
  expect(volSlider.getAttribute('min')).toBe('0');
  expect(volSlider.getAttribute('max')).toBe('100');
});

test('settings pane contains TTS on/off, volume slider, and voice picker (S8)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#set-tts-enabled')).not.toBeNull();
  expect(pane.querySelector('#set-tts-vol-slider')).not.toBeNull();
  expect(pane.querySelector('#set-voice-picker')).not.toBeNull();

  // Volume slider in settings must be 0-100.
  const slider = pane.querySelector('#set-tts-vol-slider');
  expect(slider.getAttribute('min')).toBe('0');
  expect(slider.getAttribute('max')).toBe('100');
});

// ── S9 — conversation thread strip ───────────────────────────────────────────

test('conversation pane contains thread title + New conversation button (S9)', () => {
  const pane = root.querySelector('.pane[data-route="conversation"]');
  expect(pane).not.toBeNull();
  const strip  = pane.querySelector('#conv-thread-strip');
  const title  = pane.querySelector('#conv-thread-title');
  const newBtn = pane.querySelector('#conv-thread-new');
  expect(strip).not.toBeNull();
  expect(title).not.toBeNull();
  expect(newBtn).not.toBeNull();

  // Title is editable in-place.
  expect(title.getAttribute('contenteditable')).toBe('true');
  // New-conversation control is an explicit button (not a link).
  expect(newBtn.tagName.toLowerCase()).toBe('button');
});

// ── S10 — conversations list pane ────────────────────────────────────────────

test('conversations pane has search input, thread list, and empty state (S10)', () => {
  const pane = root.querySelector('.pane[data-route="conversations"]');
  expect(pane).not.toBeNull();

  const searchEl = pane.querySelector('#conv-list-search');
  expect(searchEl).not.toBeNull();
  expect(searchEl.getAttribute('type')).toBe('search');

  const listEl = pane.querySelector('#conv-thread-list');
  expect(listEl).not.toBeNull();

  const emptyEl = pane.querySelector('#conv-list-empty');
  expect(emptyEl).not.toBeNull();
});

// ── S11 — projects (folders) toolbar in Conversations pane ───────────────────

test('conversations pane has New project button (S11)', () => {
  const pane = root.querySelector('.pane[data-route="conversations"]');
  expect(pane).not.toBeNull();
  const newBtn = pane.querySelector('#conv-new-project');
  expect(newBtn).not.toBeNull();
  expect(newBtn.tagName.toLowerCase()).toBe('button');
});

// ── S12 — Quick Ask pane ─────────────────────────────────────────────────────

test('quick-ask pane has transcript, input, send, and clear elements (S12)', () => {
  const pane = root.querySelector('.pane[data-route="quick-ask"]');
  expect(pane).not.toBeNull();

  const transcript = pane.querySelector('#qa-transcript');
  expect(transcript).not.toBeNull();

  const input = pane.querySelector('#qa-input');
  expect(input).not.toBeNull();

  const send = pane.querySelector('#qa-send');
  expect(send).not.toBeNull();
  expect(send.tagName.toLowerCase()).toBe('button');

  const clear = pane.querySelector('#qa-clear');
  expect(clear).not.toBeNull();
  expect(clear.tagName.toLowerCase()).toBe('button');
});

test('quick-ask is NOT a nav item (S5: collapsed into conversation route)', () => {
  // S5 removed quick-ask from the nav (it's now accessed via #quick-ask pane only).
  const qaNav = root.querySelector('.nav-item[data-route="quick-ask"]');
  expect(qaNav).toBeNull();
});

// ── S13 — per-conversation model override ────────────────────────────────────

test('conversation pane has model override select in thread strip (S13)', () => {
  const pane = root.querySelector('.pane[data-route="conversation"]');
  expect(pane).not.toBeNull();
  const strip    = pane.querySelector('#conv-thread-strip');
  const modelSel = pane.querySelector('#conv-thread-model');
  expect(strip).not.toBeNull();
  expect(modelSel).not.toBeNull();
  expect(modelSel.tagName.toLowerCase()).toBe('select');
  // Must include a "Global default" option (value="").
  const defaultOpt = modelSel.querySelector('option[value=""]');
  expect(defaultOpt).not.toBeNull();
});

// ── S14 — file upload controls ───────────────────────────────────────────────

test('conversation pane has attach button and drag-drop overlay (S14)', () => {
  const pane = root.querySelector('.pane[data-route="conversation"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#att-attach-btn')).not.toBeNull();
  expect(pane.querySelector('.att-dropzone')).not.toBeNull();
  expect(pane.querySelector('.att-pending-row')).not.toBeNull();
});

// ── S15 — integrations pane HARNESS section ──────────────────────────────────

test('integrations pane has HARNESS section, daemon row, and channels list (S15)', () => {
  // Content moved into the Settings pane's Sign-in sub-tab.
  const pane = root.querySelector('.set-subpane[data-set-sub="signin"]');
  expect(pane).not.toBeNull();

  // Placeholder must be gone.
  expect(pane.querySelector('.placeholder')).toBeNull();

  // HARNESS section header.
  const section = pane.querySelector('#int-harness-section');
  expect(section).not.toBeNull();

  // OpenClaw daemon row.
  const daemonRow = pane.querySelector('#int-daemon-row');
  expect(daemonRow).not.toBeNull();

  // Status dot and text inside the daemon row.
  expect(daemonRow.querySelector('#int-daemon-dot')).not.toBeNull();
  expect(daemonRow.querySelector('#int-daemon-text')).not.toBeNull();

  // Channels container.
  const channelsList = pane.querySelector('#int-channels-list');
  expect(channelsList).not.toBeNull();
});

// ── S16 — integrations pane daemon controls + channel config ────────────────

test('integrations daemon row has Start/Stop/Restart buttons (S16)', () => {
  const daemonRow = root.querySelector('#int-daemon-row');
  expect(daemonRow).not.toBeNull();

  const startBtn   = daemonRow.querySelector('#int-daemon-start');
  const stopBtn    = daemonRow.querySelector('#int-daemon-stop');
  const restartBtn = daemonRow.querySelector('#int-daemon-restart');

  expect(startBtn).not.toBeNull();
  expect(stopBtn).not.toBeNull();
  expect(restartBtn).not.toBeNull();

  expect(startBtn.getAttribute('type')).toBe('button');
  expect(stopBtn.getAttribute('type')).toBe('button');
  expect(restartBtn.getAttribute('type')).toBe('button');
});

test('inline script wires daemon control IPC events (S16)', () => {
  expect(inlineScript).toMatch(/['"]start_openclaw_daemon['"]/);
  expect(inlineScript).toMatch(/['"]stop_openclaw_daemon['"]/);
  expect(inlineScript).toMatch(/['"]restart_openclaw_daemon['"]/);
});

test('inline script wires channel enable/secret IPC events (S16)', () => {
  expect(inlineScript).toMatch(/['"]set_channel_enabled['"]/);
  expect(inlineScript).toMatch(/['"]set_channel_secret['"]/);
});

test('channel rows are collapsible and use the shared .ps-toggle switch', () => {
  // Collapsible header + persisted expansion state.
  expect(inlineScript).toMatch(/data-int-expand/);
  expect(inlineScript).toMatch(/_intExpanded/);
  // Enable/disable is the shared switch component, not a bespoke button.
  expect(inlineScript).toMatch(/ps-toggle-input/);
});

test('Permissions pane wires the computer-use full-autonomy switch (ADR-0016 S3)', () => {
  // The badged, default-off switch renders in the capabilities panel and
  // posts set_computer_use_full_autonomy on change.
  expect(inlineScript).toMatch(/data-perm-fullauto/);
  expect(inlineScript).toMatch(/['"]set_computer_use_full_autonomy['"]/);
  expect(inlineScript).toMatch(/perm-fullauto-badge/);
});

test('channel renderer uses password input for the secret field (S16)', () => {
  // The secret input must be a type="password" element so it never
  // renders the plaintext on screen. The element is created by the
  // renderer at click-time, so we grep the inline script for the markup.
  expect(inlineScript).toMatch(/type="password"/);
  // Write-only invariant: input.value is cleared BEFORE the IPC send.
  expect(inlineScript).toMatch(/input\.value\s*=\s*['"]['"]/);
});

// ── S17 — service directory ──────────────────────────────────────────────────

test('integrations pane has SERVICES section and svc body container (S17)', () => {
  const pane = root.querySelector('.set-subpane[data-set-sub="signin"]');
  expect(pane).not.toBeNull();

  const svcSection = pane.querySelector('#int-svc-section');
  expect(svcSection).not.toBeNull();

  const svcBody = pane.querySelector('#int-svc-body');
  expect(svcBody).not.toBeNull();
});

test('credential cards have anchor IDs for Connect deep-link (S17)', () => {
  const googleCard  = root.querySelector('#cred-card-google');
  const apiKeysCard = root.querySelector('#cred-card-api-keys');
  expect(googleCard).not.toBeNull();
  expect(apiKeysCard).not.toBeNull();
});

test('service-registry.js script tag is present (S17)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('service-registry'));
  expect(found).toBe(true);
});

test('inline script references service directory Connect handler (S17)', () => {
  expect(inlineScript).toMatch(/data-svc-connect/);
});

// ── S18 — channel inbox surface ──────────────────────────────────────────────

test('integrations pane has Inbox section, empty state, and list container (S18)', () => {
  const pane = root.querySelector('.set-subpane[data-set-sub="signin"]');
  expect(pane).not.toBeNull();

  const section = pane.querySelector('#int-inbox-section');
  expect(section).not.toBeNull();

  const emptyState = pane.querySelector('#int-inbox-empty');
  expect(emptyState).not.toBeNull();

  const listEl = pane.querySelector('#int-inbox-list');
  expect(listEl).not.toBeNull();
});

test('channel-inbox.js script tag is present (S18)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('channel-inbox'));
  expect(found).toBe(true);
});

test('inline script handles channel_inbox_update event and IPC verbs (S18)', () => {
  expect(inlineScript).toMatch(/['"]channel_inbox_update['"]/);
  expect(inlineScript).toMatch(/['"]request_channel_inbox['"]/);
  expect(inlineScript).toMatch(/['"]send_channel_reply['"]/);
});

test('inline script uses ChannelInbox.groupBySession for the inbox surface (S18)', () => {
  expect(inlineScript).toMatch(/ChannelInbox/);
  expect(inlineScript).toMatch(/groupBySession/);
});

test('inbox reply composer is a textarea bound to send_channel_reply (S18)', () => {
  // The composer markup is generated by renderChannelInbox at runtime.
  // The render-smoke checks the inline script string contains the marker
  // attribute the click delegate listens on.
  expect(inlineScript).toMatch(/data-int-inbox-reply/);
  expect(inlineScript).toMatch(/data-int-inbox-send/);
});

// ── S19 — recipes pane ───────────────────────────────────────────────────────

test('recipes pane has list container and empty state (S19)', () => {
  // S5: recipes content moved into library pane sub-section.
  const sub = root.querySelector('.lib-sub[data-lib="recipes"]');
  expect(sub).not.toBeNull();

  const list = sub.querySelector('#rcp-list');
  expect(list).not.toBeNull();

  const empty = sub.querySelector('#rcp-empty');
  expect(empty).not.toBeNull();
});

test('inline script handles recipes_update and recipe_run_result events (S19)', () => {
  expect(inlineScript).toMatch(/['"]recipes_update['"]/);
  expect(inlineScript).toMatch(/['"]recipe_run_result['"]/);
});

test('inline script fires run_recipe and delete_recipe IPC verbs (S19)', () => {
  expect(inlineScript).toMatch(/['"]run_recipe['"]/);
  expect(inlineScript).toMatch(/['"]delete_recipe['"]/);
  expect(inlineScript).toMatch(/['"]list_recipes['"]/);
});

// ── S20 — stop / interrupt control ───────────────────────────────────────────

test('conversation pane composer has stop-turn button (S20)', () => {
  const pane = root.querySelector('.pane[data-route="conversation"]');
  expect(pane).not.toBeNull();
  const stopBtn = pane.querySelector('#stop-turn-btn');
  expect(stopBtn).not.toBeNull();
  expect(stopBtn.tagName.toLowerCase()).toBe('button');
  // Must be hidden by default (only shown while a turn is in-flight).
  expect(stopBtn.getAttribute('hidden')).not.toBeNull();
});

test('inline script fires interrupt_turn IPC verb (S20)', () => {
  expect(inlineScript).toMatch(/['"]interrupt_turn['"]/);
});

// ── F3 — mic input device picker (#326) ──────────────────────────────────────

test('settings pane contains mic input device select (F3)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  const sel = pane.querySelector('#set-mic-device-select');
  expect(sel).not.toBeNull();
  // Must have at least one option (System default).
  const opts = pane.querySelectorAll('#set-mic-device-select option');
  expect(opts.length).toBeGreaterThanOrEqual(1);
  // First option is always "System default" (value="").
  expect(opts[0].getAttribute('value')).toBe('');
});

test('inline script calls populateMicDevices on init (F3)', () => {
  expect(inlineScript).toMatch(/populateMicDevices/);
});

test('inline script persists mic_input_device via set_setting IPC (F3)', () => {
  expect(inlineScript).toMatch(/['"]mic_input_device['"]/);
});

// ── F2 — window-resize layout (#325) ─────────────────────────────────────────

test('conversation pane is the column chain body > .content > .pane (F2)', () => {
  const pane = root.querySelector('.pane[data-route="conversation"]');
  expect(pane).not.toBeNull();
  // .pane sits inside .content, which sits inside <body>.
  let el = pane.parentNode;
  let insideContent = false;
  while (el) {
    const cls = el.classNames || '';
    const has = (name) => cls.split(/\s+/).includes(name);
    if (has('content')) insideContent = true;
    el = el.parentNode;
  }
  expect(insideContent).toBe(true);

  // The pane's children must include the thread strip, transcript, and
  // composer (the anchored header / scroll body / composer column).
  expect(pane.querySelector('#conv-thread-strip')).not.toBeNull();
  expect(pane.querySelector('#transcript')).not.toBeNull();
  expect(pane.querySelector('.composer')).not.toBeNull();
});

test('flex column chain has the min-height / min-width / overflow rules (F2)', () => {
  const html = fs.readFileSync(HTML_PATH, 'utf8');

  // .content keeps min-width:0 + overflow:hidden so the column can shrink.
  expect(html).toMatch(/\.content\s*\{[^}]*min-width:\s*0[^}]*overflow:\s*hidden/);
  // .pane keeps min-height:0 + overflow:hidden so the transcript can scroll.
  expect(html).toMatch(/\.pane\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*hidden/);
  // .transcript must declare min-height:0 -- the canonical fix for
  // flex-column scrolling, missing of which lets the transcript overflow
  // its pane at narrow heights (issue #325).
  expect(html).toMatch(/\.transcript\s*\{[^}]*min-height:\s*0/);
  // .composer and .header must wrap on narrow widths instead of letting
  // their fixed-shrink children spill past the .content clip.
  expect(html).toMatch(/\.composer\s*\{[^}]*flex-wrap:\s*wrap/);
  expect(html).toMatch(/\.header\s*\{[^}]*flex-wrap:\s*wrap/);
  // .conv-thread-strip wraps too so the title row never detaches at min size.
  expect(html).toMatch(/\.conv-thread-strip\s*\{[^}]*flex-wrap:\s*wrap/);
});

// ── F4 — voice/typed settings control (#327) ─────────────────────────────────

test('inline script handles apply_appearance broadcast (F4)', () => {
  // The settings_control plugin routes ui_scale/ui_theme/ui_accent changes
  // back through an apply_appearance broadcast; the renderer must own that
  // case in the IPC switch + persist via the existing appearance helper.
  expect(inlineScript).toMatch(/['"]apply_appearance['"]/);
  expect(inlineScript).toMatch(/handleApplyAppearance/);
});

// ── F5 — in-conversation backlog panel (#328) ─────────────────────────────────

test('conversation pane has backlog panel elements (F5)', () => {
  // The backlog panel and its inner scroll container must be in the DOM.
  expect(root.querySelector('#conv-backlog-panel')).not.toBeNull();
  expect(root.querySelector('#conv-backlog-inner')).not.toBeNull();
  expect(root.querySelector('#conv-backlog-toggle')).not.toBeNull();
  // conv-pane-body wraps the backlog panel + transcript side by side.
  expect(root.querySelector('.conv-pane-body')).not.toBeNull();
  expect(root.querySelector('.conv-pane-body > #transcript')).not.toBeNull();
});

test('inline script has backlog panel render function (F5)', () => {
  expect(inlineScript).toMatch(/renderBacklogPanel/);
  expect(inlineScript).toMatch(/applyBacklogPanelState/);
  expect(inlineScript).toMatch(/backlogPanelCollapsed/);
});

// ── boards S8 — Apply button on approved shortlist cards (#413) ──────────────

test('shortlist renders Apply only on approved cards (#413)', () => {
  // Renderer: the Apply button is emitted behind the approved ternary.
  expect(inlineScript).toMatch(/approved \? '<button class="jobs-apply-btn">Apply<\/button>' : ''/);
});

test('Apply click fires jobs_apply_start with the card URL (#413)', () => {
  expect(inlineScript).toMatch(/closest\('\.jobs-apply-btn'\)/);
  expect(inlineScript).toMatch(/type: 'jobs_apply_start', data: \{ url: applyCard\.dataset\.url \}/);
});

test('shortlist header has bulk-action buttons wired to bulk events (#419)', () => {
  expect(root.querySelector('#jobs-approve-all-btn')).not.toBeNull();
  expect(root.querySelector('#jobs-apply-all-btn')).not.toBeNull();
  expect(inlineScript).toMatch(/type: 'jobs_approve_all'/);
  expect(inlineScript).toMatch(/type: 'jobs_apply_all'/);
});

test('shortlist card titles include the company (#445)', () => {
  expect(inlineScript).toMatch(/jobs-shortlist-card-title[^]*?p\.company \? ' — ' \+ escHtml\(p\.company\)/);
});

test('application cards carry a title line resolved from postings (#433)', () => {
  expect(inlineScript).toMatch(/jobs-app-card-title/);
  expect(inlineScript).toMatch(/postingByUrl\[a\.posting_url\] \|\| postingByUrl\[a\.url\]/);
});

test('awaiting-input cards render the needs-info Q&A form (#431)', () => {
  expect(inlineScript).toMatch(/renderJobsNeedsInfoForm/);
  expect(inlineScript).toMatch(/f\.required && !f\.value && !f\.is_file_upload/);
  expect(inlineScript).toMatch(/type: 'jobs_answer_fields'/);
  // Busy-guard so a broadcast can't wipe half-typed answers.
  expect(inlineScript).toMatch(/jobsNeedsInfoFormBusy/);
});

test('apply-all sends the batch limit from the header input, default 100 (#421)', () => {
  const input = root.querySelector('#jobs-apply-limit');
  expect(input).not.toBeNull();
  expect(input.getAttribute('value')).toBe('100');
  expect(input.getAttribute('min')).toBe('1');
  expect(inlineScript).toMatch(/type: 'jobs_apply_all', data: \{ limit: limit \}/);
});

// ── S6 Documents panel (#457) ────────────────────────────────────────────────

test('documents pane has list container and empty state (S6 docs)', () => {
  // S5: documents content moved into library pane sub-section.
  const sub = root.querySelector('.lib-sub[data-lib="documents"]');
  expect(sub).not.toBeNull();

  const list  = sub.querySelector('#docs-list');
  const empty = sub.querySelector('#docs-empty');
  expect(list).not.toBeNull();
  expect(empty).not.toBeNull();
});

test('documents-panel.js script tag is present (S6 docs)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('documents-panel'));
  expect(found).toBe(true);
});

test('inline script handles documents_update event (S6 docs)', () => {
  expect(inlineScript).toMatch(/['"]documents_update['"]/);
  expect(inlineScript).toMatch(/renderDocuments/);
});

test('inline script fires list_documents on panel activation (S6 docs)', () => {
  expect(inlineScript).toMatch(/['"]list_documents['"]/);
});

test('inline script uses DocumentsPanel for rendering (S6 docs)', () => {
  expect(inlineScript).toMatch(/DocumentsPanel/);
  expect(inlineScript).toMatch(/renderList/);
});

// ── Harness S3 (#471) -- filter rail, card grid, drawer, unreachable banner ──

test('harness-panel.js script tag is present (S3 harness)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('harness-panel'));
  expect(found).toBe(true);
});

test('harness pane has filter rail, card grid, and unreachable banner (S3 harness)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  expect(pane).not.toBeNull();

  // Main layout container.
  expect(pane.querySelector('#hrns-layout')).not.toBeNull();
  // Filter rail.
  expect(pane.querySelector('#hrns-filters')).not.toBeNull();
  // Card grid.
  expect(pane.querySelector('#hrns-grid')).not.toBeNull();
  // Unreachable banner (hidden by default).
  const banner = pane.querySelector('#hrns-unreachable');
  expect(banner).not.toBeNull();
  expect(banner.getAttribute('hidden')).not.toBeNull();
  // Retry button inside banner.
  expect(pane.querySelector('#hrns-retry-btn')).not.toBeNull();
});

test('harness pane has empty and no-match states (S3 harness)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  expect(pane.querySelector('#hrns-empty')).not.toBeNull();
  expect(pane.querySelector('#hrns-no-match')).not.toBeNull();
  expect(pane.querySelector('#hrns-clear-filters')).not.toBeNull();
});

test('harness pane has detail drawer with close button (S3 harness)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  const drawer = pane.querySelector('#hrns-drawer');
  expect(drawer).not.toBeNull();
  expect(drawer.getAttribute('hidden')).not.toBeNull();
  expect(pane.querySelector('#hrns-drawer-close')).not.toBeNull();
  expect(pane.querySelector('#hrns-drawer-body')).not.toBeNull();
});

test('harness pane has search input in toolbar (S3 harness)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  const search = pane.querySelector('#hrns-search');
  expect(search).not.toBeNull();
  expect(search.getAttribute('type')).toBe('search');
});

test('inline script handles plugins:list and plugins:changed events (S3 harness)', () => {
  expect(inlineScript).toMatch(/'plugins:list'/);
  expect(inlineScript).toMatch(/'plugins:changed'/);
});

test('inline script calls renderHarness on plugin snapshot (S3 harness)', () => {
  expect(inlineScript).toMatch(/renderHarness/);
});

test('inline script sends plugins:list request on pane activation (S3 harness)', () => {
  // The plugins:list send appears at both WS-open and pane-activation.
  const count = (inlineScript.match(/'plugins:list'/g) || []).length;
  expect(count).toBeGreaterThanOrEqual(2);
});

test('inline script calls updateHarnessUnreachable on WS close (S3 harness)', () => {
  expect(inlineScript).toMatch(/updateHarnessUnreachable/);
});

test('legacy plug-main-view is hidden behind double-hidden guard (S3 harness)', () => {
  const legacyView = root.querySelector('#plug-main-view');
  expect(legacyView).not.toBeNull();
  // Must carry the hidden attribute so renderPlugins() can still reference it
  // without crashing, but never show it in the new harness layout.
  expect(legacyView.getAttribute('hidden')).not.toBeNull();
});

// ── Skills panel S5 (#542) -- sibling to Plugins under Harness ───────────────

test('action-widget.js script tag is present (S5 skills)', () => {
  const srcTags = root.querySelectorAll('script[src]');
  const found = srcTags.some(s => (s.getAttribute('src') || '').includes('action-widget'));
  expect(found).toBe(true);
});

test('harness pane has a Plugins/Skills tab strip (S5 skills)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  expect(pane).not.toBeNull();

  const tabs = pane.querySelectorAll('.hrns-tab');
  const subs = [...tabs].map(t => t.getAttribute('data-hrns-sub'));
  expect(subs).toEqual(['plugins', 'skills']);

  // Plugins is the default-active sub-view.
  const pluginsTab = pane.querySelector('.hrns-tab[data-hrns-sub="plugins"]');
  expect(pluginsTab.classList.contains('is-active')).toBe(true);
});

test('harness pane has plugins and skills sub-views (S5 skills)', () => {
  const pane = root.querySelector('.pane[data-route="harness"]');
  const pluginsSub = pane.querySelector('.hrns-sub[data-hrns-sub="plugins"]');
  const skillsSub  = pane.querySelector('.hrns-sub[data-hrns-sub="skills"]');
  expect(pluginsSub).not.toBeNull();
  expect(skillsSub).not.toBeNull();
  expect(pluginsSub.classList.contains('is-active')).toBe(true);
  expect(skillsSub.classList.contains('is-active')).toBe(false);

  // Existing Plugins-panel elements still live inside their sub-view.
  expect(pluginsSub.querySelector('#hrns-grid')).not.toBeNull();
  // Skills content is rendered from panel_spec into this container.
  expect(skillsSub.querySelector('#hrns-skills-body')).not.toBeNull();
});

test('inline script requests plugins:panel_spec for skills on tab activation (S5 skills)', () => {
  expect(inlineScript).toMatch(/harnessActivateSub/);
  expect(inlineScript).toMatch(/plugin_name:\s*'skills'/);
});

test('inline script renders the skills panel via PanelSpec.renderPanel (S5 skills)', () => {
  expect(inlineScript).toMatch(/renderSkillsPanel/);
  expect(inlineScript).toMatch(/window\.PanelSpec\.renderPanel/);
  expect(inlineScript).toMatch(/ActionWidget\.initActionWidgets/);
});

// ── Movable built-in panels (workspace secondary slot) ──────────────────────

test('inline script registers built-in library sections as workspace panels', () => {
  expect(inlineScript).toMatch(/_WS_BUILTINS/);
  for (const id of ['builtin:memory', 'builtin:insights', 'builtin:recipes', 'builtin:job-search']) {
    expect(inlineScript).toContain(`'${id}'`);
  }
});

test('borrowed sections are returned home before the slot body is cleared', () => {
  // _wsReturnBorrowed() must run before innerHTML = '' or the live section
  // node is destroyed. Assert the call ordering inside _renderWsBody.
  const m = inlineScript.match(/function _renderWsBody[^]*?_wsBodyEl\.innerHTML = ''/);
  expect(m).not.toBeNull();
  expect(m[0]).toMatch(/_wsReturnBorrowed\(\)/);
  // Placeholder marker for returning the node in document order.
  expect(inlineScript).toMatch(/data-ws-home|wsHome/);
});

test('built-in panels get no detach control and cannot detach', () => {
  // Tab loop: detach span is gated behind the builtin check.
  expect(inlineScript).toMatch(/if \(!_wsBuiltinDef\(id\)\)/);
  // _detachPanel guards builtins.
  expect(inlineScript).toMatch(/function _detachPanel[^]*?_wsBuiltinDef\(id\)[^]*?return/);
});

test('borrowed lib-sub stays visible via CSS override in the slot body', () => {
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  expect(html).toMatch(/\.ws-panel-body > \.lib-sub\s*\{[^}]*display:\s*flex/);
});

test('library tab click reclaims a borrowed section (no empty pane)', () => {
  expect(inlineScript).toMatch(/_wsReclaimBuiltin/);
  const m = inlineScript.match(/function libActivateSub[^]*?\n    \}/);
  expect(m).not.toBeNull();
  expect(m[0]).toMatch(/_wsReclaimBuiltin/);
});

// ── Pane hidden must beat per-pane display rules ─────────────────────────────

test('.pane[hidden] uses !important so later same-specificity display rules cannot unhide panes', () => {
  // .pane[data-route="library"] / [data-route="harness"] set display:flex in
  // rules AFTER .pane[hidden] with equal specificity -- without !important,
  // source order wins and those panes render stacked under the active one.
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  expect(html).toMatch(/\.pane\[hidden\]\s*\{\s*display:\s*none\s*!important/);
});

// ── Custom / remote model server (Add-model form) ────────────────────────────

test('settings models sub-pane has an Add-model form (custom remote models)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  const form = pane.querySelector('#set-add-model-form');
  expect(form).not.toBeNull();

  // Kind select with the three supported backends.
  const kind = form.querySelector('#set-add-kind');
  expect(kind).not.toBeNull();
  const kinds = Array.from(kind.querySelectorAll('option')).map((o) => o.getAttribute('value'));
  // openai leads (the common third-party-server case) so it's the default.
  expect(kinds).toEqual(['openai', 'ollama', 'anthropic']);

  // Name (connection label), URL, model, key inputs + Add button.
  expect(form.querySelector('#set-add-name')).not.toBeNull();
  expect(form.querySelector('#set-add-url')).not.toBeNull();
  expect(form.querySelector('#set-add-model-name')).not.toBeNull();
  expect(form.querySelector('#set-add-btn')).not.toBeNull();

  // The API key input must be type=password so it never renders on screen.
  const key = form.querySelector('#set-add-key');
  expect(key).not.toBeNull();
  expect(key.getAttribute('type')).toBe('password');
});

test('inline script wires add_custom_model and remove_custom_model IPC verbs', () => {
  expect(inlineScript).toMatch(/['"]add_custom_model['"]/);
  expect(inlineScript).toMatch(/['"]remove_custom_model['"]/);
  // Error event the backend returns on a failed ping is surfaced in the form.
  expect(inlineScript).toMatch(/['"]custom_model_error['"]/);
  // The Name field feeds the connection label (falls back to model, then host).
  expect(inlineScript).toMatch(/label:\s*name\s*\|\|\s*model/);
});

test('custom switch-list rows get a remove control; api key is write-only', () => {
  expect(inlineScript).toMatch(/data-remove-model/);
  expect(inlineScript).toMatch(/m\.is_custom/);
  // Key field cleared immediately after the send (never held in the DOM).
  expect(inlineScript).toMatch(/setAddKeyEl\.value\s*=\s*''/);
});

test('custom rows have an Edit control that sends edit_custom_model', () => {
  expect(inlineScript).toMatch(/data-edit-model/);
  expect(inlineScript).toMatch(/['"]edit_custom_model['"]/);
  // Edit pre-fills from the non-secret custom_configs payload.
  expect(inlineScript).toMatch(/custom_configs/);
  // A "Use for coding" toggle drives the one-step coding designation.
  expect(inlineScript).toMatch(/for_coding/);
});

// ── context_window (#760) -- optional per-model window override ─────────────

test('Add-model form has an optional context_window field (#760)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  const form = pane.querySelector('#set-add-model-form');
  expect(form).not.toBeNull();

  const ctxInput = form.querySelector('#set-add-context-window');
  expect(ctxInput).not.toBeNull();
  expect(ctxInput.getAttribute('type')).toBe('number');
  // Placeholder must communicate the 8192 default so leaving it blank reads
  // as intentional, not broken.
  expect(ctxInput.getAttribute('placeholder')).toMatch(/8192/);
});

test('inline script threads context_window into add/edit payloads and edit pre-fill', () => {
  expect(inlineScript).toMatch(/setAddContextWindowEl/);
  // Sent on both add_custom_model and edit_custom_model.
  expect(inlineScript).toMatch(/context_window:\s*setAddContextWindowEl/);
  // enterEditMode pre-fills it from the non-secret custom_configs payload.
  expect(inlineScript).toMatch(/setAddContextWindowEl\.value\s*=\s*cfg\.context_window/);
});

// ── S2 model-servers -- model discovery (Fetch button + datalist) ─────────────

test('Add-model form has a Fetch button and datalist for model suggestions (S2 model-servers)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  const form = pane.querySelector('#set-add-model-form');
  expect(form).not.toBeNull();

  // Fetch button.
  const fetchBtn = form.querySelector('#set-fetch-models-btn');
  expect(fetchBtn).not.toBeNull();
  expect(fetchBtn.tagName.toLowerCase()).toBe('button');

  // datalist bound to the model-name input.
  const datalist = form.querySelector('#set-model-suggestions');
  expect(datalist).not.toBeNull();
  expect(datalist.tagName.toLowerCase()).toBe('datalist');

  // model-name input must reference the datalist.
  const modelInput = form.querySelector('#set-add-model-name');
  expect(modelInput).not.toBeNull();
  expect(modelInput.getAttribute('list')).toBe('set-model-suggestions');
});

test('inline script wires discover_models IPC and handles models_discovered (S2 model-servers)', () => {
  expect(inlineScript).toMatch(/['"]discover_models['"]/);
  expect(inlineScript).toMatch(/['"]models_discovered['"]/);
  // Datalist populated from event data.
  expect(inlineScript).toMatch(/set-model-suggestions/);
  // Auto-fill when exactly one result.
  expect(inlineScript).toMatch(/suggestions\.length === 1/);
});

// ── S3 model-servers -- dynamic (server-first) Add form ──────────────────────

test('Add-model form accepts a blank model for ollama/openai (S3 model-servers)', () => {
  // The Add click handler must NOT block a blank model unless kind=anthropic.
  // Anthropic still requires a model (no /v1/models to resolve from).
  expect(inlineScript).toMatch(/kind === ['"]anthropic['"]/);
  expect(inlineScript).toMatch(/Model name is required for Anthropic/);
  // The pre-S3 unconditional "Model name is required." block must be gone.
  expect(inlineScript).not.toMatch(/showAddError\(['"]Model name is required\.['"]\)/);
});

test('model input placeholder hints that blank means auto (S3 model-servers)', () => {
  const modelInput = root.querySelector('#set-add-model-name');
  expect(modelInput).not.toBeNull();
  expect(modelInput.getAttribute('placeholder') || '').toMatch(/auto/i);
});

// ── P2 model-servers -- drag-drop Model priority panel ───────────────────────

test('priority panel elements exist in models sub-pane (P2 model-servers)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  const sub  = pane.querySelector('.set-subpane[data-set-sub="models"]');
  expect(sub.querySelector('#set-model-priority-list')).not.toBeNull();
  expect(sub.querySelector('#set-model-fallback-toggle')).not.toBeNull();
});

test('inline script wires set_model_priority, set_model_enabled, set_model_fallback (P2 model-servers)', () => {
  expect(inlineScript).toMatch(/['"]set_model_priority['"]/);
  expect(inlineScript).toMatch(/['"]set_model_enabled['"]/);
  expect(inlineScript).toMatch(/['"]set_model_fallback['"]/);
  // Drag verbs: dragstart, dragover, drop.
  expect(inlineScript).toMatch(/dragstart/);
  expect(inlineScript).toMatch(/dragover/);
  expect(inlineScript).toMatch(/drop/);
  // Drop indicator line on the target edge (up/down drag fix).
  expect(inlineScript).toMatch(/is-drop-before/);
  expect(inlineScript).toMatch(/is-drop-after/);
});

test('old switch_model single-select removed; per-task cards unchanged (P2 model-servers)', () => {
  // switch_model send is gone.
  expect(inlineScript).not.toMatch(/type:\s*['"]switch_model['"]/);
  // Per-task cards still present.
  expect(inlineScript).toMatch(/set-task-list/);
  expect(inlineScript).toMatch(/set_task_model/);
});

// ── Model status dots (probe_models) ─────────────────────────────────────────

test('model priority header has a Recheck button (status dots)', () => {
  const btn = root.querySelector('#set-recheck-btn');
  expect(btn).not.toBeNull();
  expect(btn.tagName.toLowerCase()).toBe('button');
});

test('inline script wires probe_models + models_health for status dots', () => {
  // Probe fired on settings-pane open and on Recheck.
  expect(inlineScript).toMatch(/['"]probe_models['"]/);
  // Health map consumed and re-rendered.
  expect(inlineScript).toMatch(/['"]models_health['"]/);
  expect(inlineScript).toMatch(/modelHealth/);
  // Each priority row carries a status dot span.
  expect(inlineScript).toMatch(/set-priority-status/);
});

// ── Issue #387 -- duplicate-profile guard error surfaced in the wizard ───────

test('profiles pane wizard has an error slot for create_profile_error (#387)', () => {
  const pane = root.querySelector('.pane[data-route="profiles"]');
  expect(pane).not.toBeNull();
  const errorEl = pane.querySelector('#prof-wizard-error');
  expect(errorEl).not.toBeNull();
  expect(errorEl.getAttribute('hidden')).not.toBeNull();
});

test('inline script handles create_profile_error and shows it in the wizard (#387)', () => {
  expect(inlineScript).toMatch(/['"]create_profile_error['"]/);
  expect(inlineScript).toMatch(/showProfWizardError/);
  // Duplicate refusal must not be treated as a successful profile_loaded --
  // the case must not fall through into collapsing the wizard / clearing
  // first-run mode.
  const m = inlineScript.match(/case 'create_profile_error':[^]*?break;/);
  expect(m).not.toBeNull();
  expect(m[0]).not.toMatch(/profCollapseWizard/);
});

// ── boards S4 (#405) -- ATS badge, appliable filter, ATS-aware search ───────

test('"Appliable now" filter checkbox exists above the postings list', () => {
  const checkbox = root.querySelector('#jobs-appliable-filter');
  expect(checkbox).not.toBeNull();
  expect(checkbox.getAttribute('type')).toBe('checkbox');
  // Must sit inside the job-search sub-pane, immediately ahead of #jobs-list.
  const sub = root.querySelector('.lib-sub[data-lib="job-search"]');
  expect(sub).not.toBeNull();
  expect(sub.querySelector('#jobs-appliable-filter')).not.toBeNull();
  expect(sub.querySelector('#jobs-list')).not.toBeNull();
});

test('checking the filter toggles jobsAppliableOnly and re-renders (#405)', () => {
  expect(inlineScript).toMatch(/jobsAppliableFilterEl\.addEventListener\('change'/);
  expect(inlineScript).toMatch(/jobsAppliableOnly = jobsAppliableFilterEl\.checked/);
});

test('renderJobSearch filters postings to p.appliable when the toggle is on (#405)', () => {
  const m = inlineScript.match(/function renderJobSearch\(\)[^]*?\n {4}\}/);
  expect(m).not.toBeNull();
  expect(m[0]).toMatch(/jobsAppliableOnly[^]*?p\.appliable/);
});

test('each posting card renders an ATS badge sourced from p.ats_type / p.appliable (#405)', () => {
  expect(inlineScript).toMatch(/jobs-ats-badge/);
  expect(inlineScript).toMatch(/jobsAtsBadgeLabel\(p\.ats_type\)/);
  expect(inlineScript).toMatch(/p\.appliable \? ' is-appliable' : ''/);
});

test('unsupported/unknown ATS types badge as an em dash, greenhouse/lever get display names (#405)', () => {
  const m = inlineScript.match(/var _ATS_BADGE_LABEL = \{[^}]*\}/);
  expect(m).not.toBeNull();
  expect(m[0]).toMatch(/greenhouse:\s*'Greenhouse'/);
  expect(m[0]).toMatch(/lever:\s*'Lever'/);
  expect(inlineScript).toMatch(/return _ATS_BADGE_LABEL\[atsType\] \|\| '—'/);
});

test('posting detail card shows an ATS host line, or the ats_note when no ATS link was found (#405)', () => {
  expect(inlineScript).toMatch(/jobs-card-ats/);
  const m = inlineScript.match(/function jobsAtsDetailLine\(p\)[^]*?\n {4}\}/);
  expect(m).not.toBeNull();
  // No-ATS-link case (S3 #404 ats_note) takes priority over the host line.
  expect(m[0]).toMatch(/if \(p\.ats_note\) return 'No ATS link: '/);
  expect(m[0]).toMatch(/new URL\(p\.url\)\.host/);
});

test('pane search for job-search filters the postings list too, not just the shortlist (#405)', () => {
  const m = inlineScript.match(/_registerInPaneFilter\('job-search'[^]*?\n {4}\}\);/);
  expect(m).not.toBeNull();
  expect(m[0]).toMatch(/jobs-shortlist-card/);
  // No labelSelector on the postings-card filter -- matches the whole card
  // text (title, company, the rendered "ATS host: <host>" line, and the
  // badge label), so typing "greenhouse" or a host substring finds it.
  expect(m[0]).toMatch(/_filterRowsByText\('#jobs-list \.jobs-card', null, q\)/);
});

// ── Script syntax ────────────────────────────────────────────────────────────

test('inline script parses without syntax errors', () => {
  expect(() => new vm.Script(inlineScript)).not.toThrow();
});

// ── Artifact ─────────────────────────────────────────────────────────────────

test('writes serialised-DOM artifact', () => {
  const panesFound = SMOKE_ROUTES.filter(r => root.querySelector(`.pane[data-route="${r}"]`));
  const navFound   = SMOKE_ROUTES.filter(r => root.querySelector(`.nav-item[data-route="${r}"]`));

  const artifact = {
    timestamp:       new Date().toISOString(),
    html_path:       HTML_PATH,
    routes_checked:  SMOKE_ROUTES,
    panes_found:     panesFound,
    nav_items_found: navFound,
    script_bytes:    inlineScript ? inlineScript.length : 0,
  };

  fs.writeFileSync(path.join(ARTIFACT_DIR, 'last-run.json'),
    JSON.stringify(artifact, null, 2));

  const navEl = root.querySelector('.nav');
  if (navEl) {
    fs.writeFileSync(path.join(ARTIFACT_DIR, 'sidebar-nav.html'), navEl.outerHTML);
  }

  expect(fs.existsSync(path.join(ARTIFACT_DIR, 'last-run.json'))).toBe(true);
});
