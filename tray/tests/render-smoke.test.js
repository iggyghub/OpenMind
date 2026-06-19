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

// Routes that must have both a pane and a nav item in the current baseline.
// Extended in S2 with models/conversations/integrations/recipes.
const SMOKE_ROUTES = [
  'conversation', 'quick-ask', 'queue', 'insights', 'memory',
  'permissions', 'credentials', 'plugins', 'profiles', 'settings',
  'models', 'conversations', 'integrations', 'recipes',
];

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
  for (const route of SMOKE_ROUTES) {
    const pane = root.querySelector(`.pane[data-route="${route}"]`);
    expect(pane).not.toBeNull();
  }
});

// ── Nav items ────────────────────────────────────────────────────────────────

test('every expected nav item exists', () => {
  for (const route of SMOKE_ROUTES) {
    const nav = root.querySelector(`.nav-item[data-route="${route}"]`);
    expect(nav).not.toBeNull();
  }
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

// ── S5 — models pane has model controls; settings pane does not ──────────────

test('models pane contains model control elements (S5)', () => {
  const pane = root.querySelector('.pane[data-route="models"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#set-active-header')).not.toBeNull();
  expect(pane.querySelector('#set-model-list')).not.toBeNull();
  expect(pane.querySelector('#set-task-list')).not.toBeNull();
  expect(pane.querySelector('#set-refresh-btn')).not.toBeNull();
});

test('settings pane no longer contains model control elements (S5)', () => {
  const pane = root.querySelector('.pane[data-route="settings"]');
  expect(pane).not.toBeNull();
  expect(pane.querySelector('#set-model-list')).toBeNull();
  expect(pane.querySelector('#set-refresh-btn')).toBeNull();
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

test('quick-ask nav item is inside the CHAT section (S12)', () => {
  const qaNav   = root.querySelector('.nav-item[data-route="quick-ask"]');
  const convNav = root.querySelector('.nav-item[data-route="conversation"]');
  expect(qaNav).not.toBeNull();
  expect(convNav).not.toBeNull();
  // Both must share the same parent nav-section (CHAT).
  expect(qaNav.parentNode).toBe(convNav.parentNode);
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
  const pane = root.querySelector('.pane[data-route="integrations"]');
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
  const pane = root.querySelector('.pane[data-route="integrations"]');
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
  const pane = root.querySelector('.pane[data-route="integrations"]');
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
  const pane = root.querySelector('.pane[data-route="recipes"]');
  expect(pane).not.toBeNull();

  // Placeholder must be gone.
  expect(pane.querySelector('.placeholder')).toBeNull();

  const list = pane.querySelector('#rcp-list');
  expect(list).not.toBeNull();

  const empty = pane.querySelector('#rcp-empty');
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
