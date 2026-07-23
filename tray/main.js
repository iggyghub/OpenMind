const { app, Tray, Menu, BrowserWindow, Notification, nativeImage, ipcMain, screen, shell } = require('electron');
const WebSocket = require('ws');
const path = require('path');
const { VisualiserState }      = require('./lib/visualiser-state');
const { PositionStore }        = require('./lib/position-store');
const { NotificationManager }  = require('./lib/notification-manager');
const { ModalManager }         = require('./lib/modal-manager');
// PermissionsStore is no longer instantiated in main.js (Issue #202).
// The Main window's renderer loads it directly via a <script src> tag
// — same source file, dual-mode export. tray/tests/permissions-store.test.js
// still require()s it for the Node test suite.

const CEREBRAL_URL    = 'ws://localhost:7766';
const ICON_PATH       = path.join(__dirname, 'assets', 'icon.png');
const ICO_PATH        = path.join(__dirname, 'assets', 'icon.ico');
const VIS_POS_PATH    = path.join(__dirname, '..', 'cerebral', 'data', 'visualiser-pos.json');
const LAUNCHER_LOG    = path.join(__dirname, '..', 'launcher.log');
const CEREBRAL_LOG    = path.join(__dirname, '..', 'cerebral.log');
const RECONNECT_DELAY_MS = 3000;

let tray = null;
let ws = null;
let isConnected = false;
let isQuitting  = false;
let reconnectTimer = null;
let felixState  = 'idle';      // 'idle' | 'active'
let activeProfile = null;
let allProfiles   = [];
let mainWindow        = null;
let visualiserWindow  = null;
// request_id → BrowserWindow for the irreversible-flag modal (Issue #49).
const modalWindows = new Map();

const visState   = new VisualiserState();
const posStore   = new PositionStore(VIS_POS_PATH);

// In-memory cache of the settings_updated snapshot from Cerebral.
// Starts from defaults; overwritten on first broadcast (sent on every connect).
let settingsCache = {
  notifications_enabled:     false,
  reminder_interval_minutes: 120,
  camera_enabled:            false,
  visualiser_visible:        false,
};

function electronNotify(title, body, onClick) {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body });
  if (onClick) n.on('click', onClick);
  n.show();
}

const notifManager = new NotificationManager({
  onPersist:            (key, value) => sendToCerebral({ type: 'set_setting', data: { key, value } }),
  notify:               electronNotify,
  onNotificationClick:  () => openMainWindow('#queue'),
});

const modalManager = new ModalManager({
  send:         (event) => sendToCerebral(event),
  openPrompt:   (record) => openModalWindow(record),
  closePrompt:  (request_id) => closeModalWindow(request_id),
});

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectToCerebral() {
  if (isQuitting) return;

  ws = new WebSocket(CEREBRAL_URL);

  ws.on('open', () => {
    isConnected = true;
    console.log('[tray] Connected to Cerebral');
    refreshMenu();
  });

  ws.on('message', (data) => {
    try {
      const event = JSON.parse(data.toString());
      handleCerebralEvent(event);
    } catch {
      console.error('[tray] Unparseable message:', data.toString());
    }
  });

  ws.on('close', () => {
    isConnected = false;
    // Cerebral disconnected — close any open irreversible modals so the
    // user isn't left clicking buttons that go nowhere (the request will
    // time out on the Cerebral side and DENY). Inline consent cards in
    // the Main window renderer are handled by the renderer's own WS close.
    modalManager.reset();
    if (!isQuitting) {
      console.log(`[tray] Disconnected — retrying in ${RECONNECT_DELAY_MS}ms`);
      refreshMenu();
      reconnectTimer = setTimeout(connectToCerebral, RECONNECT_DELAY_MS);
    }
  });

  ws.on('error', (err) => {
    if (err.code !== 'ECONNREFUSED') {
      console.error('[tray] WebSocket error:', err.message);
    }
  });
}

function sendToCerebral(event) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(event));
  }
}

// ── Cerebral event handling ───────────────────────────────────────────────────

function handleCerebralEvent(event) {
  switch (event.type) {
    case 'first_run':
      // Profile-setup popup retired in Issue #204 — open the Main
      // window's Profiles pane; the renderer flips into first-run
      // wizard mode itself on the same event.
      openMainWindow('#profiles');
      break;

    case 'profile_loaded':
      activeProfile = event.data;
      refreshMenu();
      console.log('[tray] Profile loaded:', activeProfile.name);
      break;

    case 'profiles_list':
      allProfiles = event.data.profiles || [];
      refreshMenu();
      break;

    case 'wake':
      felixState = 'active';
      refreshMenu();
      routeToVisualiser(event);
      break;

    case 'thinking':
      routeToVisualiser(event);
      break;

    case 'passive':
      felixState = 'idle';
      refreshMenu();
      routeToVisualiser(event);
      break;

    case 'tts_speaking':
      routeToVisualiser(event);
      break;

    case 'tts_done':
      routeToVisualiser(event);
      break;

    case 'heartbeat':
      break;

    case 'queue_update':
      // The Main window renderer subscribes to the same broadcast directly
      // over WS (Issue #194). main.js only forwards to NotificationManager
      // for OS reminder notifications.
      notifManager.handleQueueUpdate((event.data && event.data.items) || []);
      break;

    case 'open_felix':
      // #441 — the launcher was invoked while Felix is already running:
      // surface the hidden window instead of looking like a dead click.
      openMainWindow();
      break;

    case 'user_notification': {
      // A direct, user-facing OS notification from Cerebral (a browser
      // sign-in wall, an apply outcome). NOT gated by notifications_enabled
      // (#441): that toggle governs recurring queue reminders; these are
      // deliberate one-shot messages — dropping them silently blinded the
      // whole live apply ramp for users with reminders off.
      const d = event.data || {};
      electronNotify(d.title || 'Felix', d.body || '', () => openMainWindow());
      break;
    }

    case 'queue_item_result':
      // Routed straight to the Main window renderer via the shared WS
      // since Issue #194; main.js no longer forwards it.
      break;

    case 'insights_update':
      // Routed straight to the Main window renderer via the shared WS
      // since Issue #196; main.js no longer mirrors or forwards it.
      break;

    case 'memory_update':
      // Routed straight to the Main window renderer via the shared WS
      // since Issue #198; main.js no longer mirrors or forwards it.
      break;
    case 'env_context_update':
      break;

    case 'settings_updated': {
      const prev = settingsCache;
      settingsCache = event.data || {};
      notifManager.applySettings(settingsCache);
      // Sync visualiser visibility if it changed.
      const visNow = !!settingsCache.visualiser_visible;
      if (visNow !== !!prev.visualiser_visible) {
        if (visNow && !visState.visible) {
          visState.toggle();
          openVisualiserWindow();
        } else if (!visNow && visState.visible) {
          visState.toggle();
          if (visualiserWindow && !visualiserWindow.isDestroyed()) {
            saveVisualiserPosition();
            visualiserWindow.close();
          }
        }
      }
      refreshMenu();
      break;
    }

    case 'models_list':
    case 'model_switched':
      break;

    case 'model_switching':
      routeToVisualiser(event);
      break;

    case 'consent_request': {
      // Issue #189 — ask-class consent cards now live in the Main window's
      // Conversation pane (inline cards) per ADR-0007 Slice 3. Ensure the
      // window is open, then fire an OS notification if it isn't focused so
      // the user knows a consent card appeared.
      const d = event.data || {};
      openMainWindow();
      if (!mainWindow || mainWindow.isDestroyed() || !mainWindow.isFocused()) {
        electronNotify(
          'Felix needs permission',
          d.capability_label || d.capability || 'Tool call pending approval',
          () => openMainWindow(),
        );
      }
      break;
    }

    case 'irreversible_modal_request':
      modalManager.handleModalRequest(event.data || {});
      break;

    case 'permissions_state':
    case 'tools_list':
    case 'plugins_list':
      // Routed straight to the Main window renderer via the shared WS
      // since Issue #202; main.js no longer mirrors or forwards them.
      break;

    case 'credentials_state':
      // Routed straight to the Main window renderer via the shared WS
      // since Issue #200; main.js no longer mirrors or forwards it.
      break;
  }
}

// Forward a Cerebral event to the visualiser window and update state machine
function routeToVisualiser(event) {
  const { state, changed } = visState.handleEvent(event);
  if (changed && visualiserWindow && !visualiserWindow.isDestroyed()) {
    visualiserWindow.webContents.send('visualiser:state', state);
  }
}

// Profile-setup popup retired in Issue #204 — the wizard lives in the
// Main window's Profiles pane now and submits create_profile straight
// to Cerebral over the shared WebSocket. The tray menu's "New profile…"
// item and Cerebral's first_run event both deep-link into
// `main.html#profiles` via openMainWindow('#profiles'). The renderer
// flips the pane into first-run mode (wizard-only, list hidden) on
// receipt of first_run and back out on profile_loaded.

// ── Main window (Issue #185 / ADR-0007) ───────────────────────────────────────
//
// Chat-primary Felix UI. Unlike the legacy per-surface tray windows, the
// renderer talks WebSocket directly to Cerebral (no ipcRenderer / no
// nodeIntegration) per the ADR-0007 renderer-portability invariant. main.js
// is only responsible for window lifecycle here.

function openMainWindow(hash) {
  // `hash` is the optional deep-link route (without the `#`). The Main
  // window's router activates the matching pane on hashchange, so callers
  // like the tray menu's "Queue" item can route into a non-default pane.
  const hashStr = hash ? hash.replace(/^#/, '') : '';

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    mainWindow.focus();
    if (hashStr) {
      // Use a hashchange so the renderer's router runs through the same
      // path as a sidebar click. We can't use ipcRenderer (contextIsolation
      // + nodeIntegration:false per ADR-0007), and Cerebral isn't the
      // right transport for a renderer-local navigation.
      mainWindow.webContents.executeJavaScript(
        `location.hash = ${JSON.stringify('#' + hashStr)};`,
      ).catch(() => {});
    }
    return;
  }

  mainWindow = new BrowserWindow({
    width:           1200,
    height:          800,
    minWidth:        720,
    minHeight:       480,
    title:           'Felix',
    icon:            ICO_PATH,
    backgroundColor: '#12101e',
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'windows', 'main.html'),
    hashStr ? { hash: hashStr } : undefined);

  // UI2 A5 (#485) -- detached panels open via window.open('detached-panel.html')
  // from the renderer. Whitelist that one file, deny everything else, and
  // force the same webPreferences posture as the Main window (ADR-0007).
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    let ok = false;
    try {
      const parsed = new URL(url);
      ok = parsed.protocol === 'file:'
        && parsed.pathname.replace(/\\/g, '/').endsWith('/tray/windows/detached-panel.html');
    } catch (_) { ok = false; }
    if (!ok) return { action: 'deny' };
    return {
      action: 'allow',
      overrideBrowserWindowOptions: {
        width:           720,
        height:          560,
        minWidth:        320,
        minHeight:       240,
        title:           'Felix — Panel',
        icon:            ICO_PATH,
        backgroundColor: '#12101e',
        webPreferences: {
          nodeIntegration:  false,
          contextIsolation: true,
        },
      },
    };
  });

  // Issue #188 — close button hides to tray; quit is tray-only.
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

// Queue popup retired in Issue #194 — the Queue lives in the Main window's
// sidebar pane now and talks directly to the Cerebral WebSocket.

// Insights popup retired in Issue #196 — the Insights view lives in the
// Main window's sidebar pane now and talks directly to the Cerebral
// WebSocket. The tray menu's "Insights" item deep-links into
// `main.html#insights` via openMainWindow('#insights').

// Memory popup retired in Issue #198 — the Memory view lives in the Main
// window's sidebar pane now and talks directly to the Cerebral WebSocket.
// The tray menu's "Memory" item deep-links into `main.html#memory` via
// openMainWindow('#memory').

// Permissions popup retired in Issue #202 — the Capabilities / Tools
// tabs live in the Main window's sidebar pane now and talk directly to
// the Cerebral WebSocket (the PermissionsStore loads via a <script src>
// from the renderer; dual-mode export keeps the Node test suite green).
// The tray menu's "Permissions" item deep-links into
// `main.html#permissions` via openMainWindow('#permissions').

// Credentials popup retired in Issue #200 — the Connected-accounts +
// API-keys cards live in the Main window's sidebar pane now and talk
// directly to the Cerebral WebSocket. The tray menu's "Credentials"
// item deep-links into `main.html#credentials` via
// openMainWindow('#credentials'). Write-only contract on the client
// secret + static-token values is preserved in the renderer (DOM
// cleared on send; credentials_state never carries values back).

// ── Irreversible-flag modal (Issue #49) ───────────────────────────────────────
//
// Sibling to the consent prompt above but with a strictly two-button
// vocabulary (Accept / Cancel) and a louder visual treatment. Per the
// sharpener: the visualiser is a 200x200 transparent click-through
// window that can't host a modal, so this opens a dedicated 420x320
// BrowserWindow anchored to the visualiser UX but standalone in
// practice. Acceptance is one-shot, never persisted (AC#4).

function openModalWindow(record) {
  const existing = modalWindows.get(record.request_id);
  if (existing && !existing.isDestroyed()) {
    existing.webContents.send('irreversible-modal:show', record);
    existing.focus();
    return;
  }

  const win = new BrowserWindow({
    width:           420,
    height:          320,
    resizable:       false,
    title:           'Felix — Confirm',
    icon:            ICO_PATH,
    backgroundColor: '#12101e',
    alwaysOnTop:     true,
    skipTaskbar:     true,
    webPreferences: {
      nodeIntegration:  true,
      contextIsolation: false,
    },
  });

  win.setMenuBarVisibility(false);
  modalWindows.set(record.request_id, win);

  win.webContents.once('did-finish-load', () => {
    win.webContents.send('irreversible-modal:show', record);
  });
  win.loadFile(path.join(__dirname, 'windows', 'irreversible-modal.html'));

  win.on('closed', () => {
    modalWindows.delete(record.request_id);
    // External close (user hit the OS X button) before the user picked
    // a button — treat as Cancel so the manager cleans up and Cerebral
    // gets a response promptly.
    if (modalManager.get(record.request_id)) {
      modalManager.respond(record.request_id, 'cancel');
    }
  });
}

function closeModalWindow(request_id) {
  const win = modalWindows.get(request_id);
  if (win && !win.isDestroyed()) {
    modalWindows.delete(request_id);
    win.close();
  }
}

ipcMain.on('irreversible-modal:choose', (_event, { request_id, choice }) => {
  modalManager.respond(request_id, choice);
});

ipcMain.on('irreversible-modal:ready', (event) => {
  for (const [request_id, win] of modalWindows.entries()) {
    if (!win.isDestroyed() && win.webContents === event.sender) {
      const record = modalManager.get(request_id);
      if (record) win.webContents.send('irreversible-modal:show', record);
      return;
    }
  }
});

// ── Visualiser window ─────────────────────────────────────────────────────────

function openVisualiserWindow() {
  if (visualiserWindow && !visualiserWindow.isDestroyed()) {
    visualiserWindow.focus();
    return;
  }

  const savedPos = posStore.load();
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  // Default: bottom-right corner with a small margin
  const defaultPos = { x: sw - 220, y: sh - 220 };
  const pos = savedPos || defaultPos;

  visualiserWindow = new BrowserWindow({
    width:  200,
    height: 200,
    x: pos.x,
    y: pos.y,
    frame:           false,
    transparent:     true,
    alwaysOnTop:     true,
    skipTaskbar:     true,
    resizable:       false,
    hasShadow:       false,
    roundedCorners:  false,
    icon:            ICO_PATH,
    webPreferences: {
      nodeIntegration:  true,
      contextIsolation: false,
    },
  });

  // Click-through: mouse events pass to whatever is underneath
  visualiserWindow.setIgnoreMouseEvents(true);
  visualiserWindow.setMenuBarVisibility(false);
  visualiserWindow.loadFile(path.join(__dirname, 'windows', 'visualiser.html'));

  // Sync current state immediately after load
  visualiserWindow.webContents.once('did-finish-load', () => {
    visualiserWindow.webContents.send('visualiser:state', visState.state);
  });

  // Persist position when dragged (user can drag via –webkit-app-region if needed)
  visualiserWindow.on('moved', saveVisualiserPosition);

  visualiserWindow.on('closed', () => {
    visualiserWindow = null;
    if (visState.visible) {
      // Window was closed externally — keep state in sync
      visState.toggle(); // flip back to hidden
    }
    refreshMenu();
  });
}

function saveVisualiserPosition() {
  if (!visualiserWindow || visualiserWindow.isDestroyed()) return;
  const [x, y] = visualiserWindow.getPosition();
  posStore.save({ x, y });
}

function toggleVisualiser() {
  const { visible } = visState.toggle();
  sendToCerebral({ type: 'set_setting', data: { key: 'visualiser_visible', value: visible } });
  refreshMenu();

  if (visible) {
    openVisualiserWindow();
  } else {
    if (visualiserWindow && !visualiserWindow.isDestroyed()) {
      saveVisualiserPosition();
      visualiserWindow.close();
    }
  }
}

// ── Tray menu ─────────────────────────────────────────────────────────────────

function buildMenu() {
  const template = [];

  // 1. Status
  if (!isConnected) {
    template.push({ label: 'Felix — Connecting...', enabled: false });
  } else if (felixState === 'active') {
    template.push({ label: 'Felix — ACTIVE — listening', enabled: false });
  } else {
    template.push({ label: 'Felix — Running', enabled: false });
  }

  template.push({ type: 'separator' });

  // 2. Open Felix
  template.push({ label: 'Open Felix', click: () => openMainWindow() });

  // 3. Switch profile (only when multiple profiles exist)
  if (activeProfile && allProfiles.length > 1) {
    const switchItems = allProfiles.map((p) => ({
      label:   p.name,
      type:    'radio',
      checked: p.id === activeProfile.id,
      click:   () => sendToCerebral({ type: 'switch_profile', data: { id: p.id } }),
    }));
    template.push({ label: 'Switch profile', submenu: switchItems });
  }

  template.push({ type: 'separator' });

  // 4. Logs + Quit
  template.push({
    label: 'Show Logs',
    submenu: [
      { label: 'Launcher log',  click: () => shell.openPath(LAUNCHER_LOG) },
      { label: 'Cerebral log',  click: () => shell.openPath(CEREBRAL_LOG) },
    ],
  });
  template.push({ label: 'Restart Felix', click: restartFelix });  // #439
  template.push({ label: 'Quit', click: quit });

  return Menu.buildFromTemplate(template);
}

function refreshMenu() {
  if (!tray) return;

  const tooltip = !isConnected
    ? 'Felix — connecting...'
    : felixState === 'active'
      ? 'Felix — ACTIVE'
      : activeProfile
        ? `Felix — ${activeProfile.name}`
        : 'Felix — running';

  tray.setToolTip(tooltip);
  tray.setContextMenu(buildMenu());
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

function quit() {
  isQuitting = true;
  clearTimeout(reconnectTimer);
  notifManager.destroy();
  saveVisualiserPosition();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'shutdown' }));
    ws.close();
  }
  app.quit();
}

// #439/#443 — one-click full restart. A detached child spawned from a
// QUITTING Electron parent dies silently on this box (bit us twice: #443 and
// again 2026-07-23, launcher never even logged), so the dying process no
// longer spawns anything. Instead: app.relaunch() — Chromium re-execs the
// tray AFTER this process exits, which is the platform primitive for exactly
// this — and the freshly booted (stable, long-lived) instance sees
// --felix-restart in argv and spawns the launcher to reboot Cerebral only.
function trayLog(msg) {
  const fs = require('fs');
  try { fs.appendFileSync(LAUNCHER_LOG, `[tray] ${msg}\n`); } catch (_) {}
}

function restartFelix() {
  trayLog('restart: relaunching tray via app.relaunch()');
  app.relaunch({ args: process.argv.slice(1).concat(['--felix-restart']) });
  quit(); // sends Cerebral the shutdown event, then app.quit()
}

// Runs in the relaunched instance: reboot Cerebral. -Restart makes the
// launcher wait for :7766 to free (old Cerebral tearing down); -CerebralOnly
// keeps it from starting a second tray — this instance IS the tray.
function respawnCerebral() {
  try {
    const { spawn } = require('child_process');
    const launcher = path.join(__dirname, '..', 'scripts', 'launch-felix.ps1');
    const psExe = path.join(
      process.env.SystemRoot || 'C:\\Windows',
      'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe',
    );
    const child = spawn(psExe,
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', launcher,
       '-Restart', '-CerebralOnly'],
      { detached: true, stdio: 'ignore', windowsHide: true },
    );
    child.on('error', (err) => trayLog(`cerebral respawn error: ${err}`));
    child.unref();
    trayLog(`restart: relaunched tray up, spawned launcher for Cerebral (pid ${child.pid})`);
  } catch (e) {
    trayLog(`cerebral respawn threw: ${e}`);
  }
}

app.whenReady().then(() => {
  if (app.dock) app.dock.hide();
  app.setName('Felix');

  // Second half of "Restart Felix" (#443 rework): this instance was
  // relaunched by restartFelix(); Cerebral is down — bring it back.
  if (process.argv.includes('--felix-restart')) respawnCerebral();

  // #439 — Main window menu bar: File gains Restart/Quit (the window's X
  // only hides to tray per #188, so these need a discoverable home).
  // Standard Edit/View roles keep copy/paste and zoom shortcuts alive.
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'File',
      submenu: [
        { label: 'Restart Felix', click: restartFelix },
        { label: 'Quit Felix', click: quit },
      ],
    },
    { role: 'editMenu' },
    { role: 'viewMenu' },
  ]));

  const icon = nativeImage.createFromPath(ICON_PATH);
  tray = new Tray(icon);

  tray.on('click', () => openMainWindow());

  refreshMenu();
  connectToCerebral();
  console.log('[tray] Felix tray started');

  // Issue #185 follow-on: fire an OS notification on startup. The Felix
  // tray icon goes to the Win10 hidden-icons overflow by default and is
  // easy to miss; a toast makes "the tray is alive" undeniable on first
  // launch. Bypasses NotificationManager (which is for queued-action
  // reminders) -- this is a one-shot lifecycle signal.
  if (Notification.isSupported()) {
    const n = new Notification({
      title: 'Felix is running',
      body:  'Click ^ in your system tray to find the Felix icon. Right-click it for the menu.',
    });
    n.on('click', () => openMainWindow());
    n.show();
  }
});

app.on('window-all-closed', () => {
  // Keep alive as tray-only app
});

app.on('before-quit', () => {
  isQuitting = true;
});
