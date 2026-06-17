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
  'conversation', 'queue', 'insights', 'memory',
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
