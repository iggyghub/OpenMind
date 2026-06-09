const { app, Tray, Menu, BrowserWindow, Notification, nativeImage, ipcMain, screen } = require('electron');
const WebSocket = require('ws');
const path = require('path');
const { VisualiserState }      = require('./lib/visualiser-state');
const { PositionStore }        = require('./lib/position-store');
const { SettingsStore }        = require('./lib/settings-store');
const { NotificationManager }  = require('./lib/notification-manager');
const { ConsentManager }       = require('./lib/consent-manager');
const { ModalManager }         = require('./lib/modal-manager');
const { PermissionsStore }     = require('./lib/permissions-store');
const { buildModelSubmenu }    = require('./lib/model-menu');

const CEREBRAL_URL    = 'ws://localhost:7766';
const ICON_PATH       = path.join(__dirname, 'assets', 'icon.png');
const VIS_POS_PATH    = path.join(__dirname, '..', 'cerebral', 'data', 'visualiser-pos.json');
const SETTINGS_PATH   = path.join(__dirname, '..', 'cerebral', 'data', 'felix-settings.json');
const RECONNECT_DELAY_MS = 3000;

let tray = null;
let ws = null;
let isConnected = false;
let isQuitting  = false;
let reconnectTimer = null;
let felixState  = 'idle';      // 'idle' | 'active'
let activeProfile = null;
let allProfiles   = [];
let pendingItems  = [];
let setupWindow      = null;
let mainWindow        = null;
let visualiserWindow  = null;
let permissionsWindow = null;
let credentialsWindow = null;
// Latest snapshots from Cerebral, kept up-to-date so a freshly-opened
// Permissions window doesn't render a blank state while it waits for
// the next broadcast. The store inside the window is fed by these on
// `permissions:ready` and re-fed on every subsequent broadcast.
let permissionsState  = null;
// Latest connected-account status (Issue #114). Cached so a re-opened
// Credentials window renders immediately rather than waiting for the
// next Cerebral broadcast.
let credentialsState  = null;
let toolsList         = [];
let pluginsList       = [];
// request_id → BrowserWindow for the consent prompt (Issue #48). Each
// outstanding ASK from Cerebral gets its own window so per-class prompts
// can be shown side-by-side.
const consentWindows = new Map();
// request_id → BrowserWindow for the irreversible-flag modal (Issue #49).
// Separate map from the consent windows so the two surfaces never collide.
const modalWindows = new Map();
let envContext       = {};
let modelsList       = [];
let activeModel      = null;
let lastModel        = null;
let activeIsCloud    = false;
let taskModels       = {};

const visState      = new VisualiserState();
const posStore      = new PositionStore(VIS_POS_PATH);
const settingsStore = new SettingsStore(SETTINGS_PATH, {
  notifications_enabled:     false,
  reminder_interval_minutes: 120,
  camera_enabled:            false,
});

function electronNotify(title, body, onClick) {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body });
  if (onClick) n.on('click', onClick);
  n.show();
}

const notifManager = new NotificationManager({
  store:                settingsStore,
  notify:               electronNotify,
  onNotificationClick:  () => openMainWindow('#queue'),
});

const consentManager = new ConsentManager({
  send:         (event) => sendToCerebral(event),
  openPrompt:   (record) => openConsentWindow(record),
  closePrompt:  (request_id) => closeConsentWindow(request_id),
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
    // Sync persisted camera state to Cerebral on reconnect
    const camEnabled = settingsStore.get('camera_enabled');
    sendToCerebral({ type: 'set_camera_enabled', data: { enabled: camEnabled } });
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
    // Cerebral disconnected — any open consent prompts or irreversible
    // modals can no longer be satisfied (the request will time out on
    // the Cerebral side and DENY). Close them so the user isn't left
    // clicking buttons that go nowhere.
    consentManager.reset();
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
      openSetupWindow();
      break;

    case 'profile_loaded':
      activeProfile = event.data;
      closeSetupWindow();
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
      // Mirror the latest pending list so the tray icon's tooltip + count
      // stay current. The Main window's renderer subscribes to the same
      // broadcast directly over WS (Issue #194) — no need to forward it.
      pendingItems = (event.data && event.data.items) || [];
      notifManager.handleQueueUpdate(pendingItems);
      refreshMenu();
      break;

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
      envContext = (event.data && event.data.context) || {};
      refreshMenu();
      break;

    case 'models_list': {
      const d = event.data || {};
      modelsList    = d.models || [];
      activeModel   = d.active || null;
      lastModel     = d.last || null;
      activeIsCloud = !!d.active_is_cloud;
      taskModels    = d.task_models || {};
      refreshMenu();
      break;
    }

    case 'model_switched':
      // The follow-up models_list event will refresh the menu; route the
      // model_switching event to the visualiser so it briefly shows thinking.
      break;

    case 'model_switching':
      routeToVisualiser(event);
      break;

    case 'consent_request':
      consentManager.handleConsentRequest(event.data || {});
      break;

    case 'irreversible_modal_request':
      modalManager.handleModalRequest(event.data || {});
      break;

    case 'permissions_state':
      permissionsState = event.data || null;
      if (permissionsWindow && !permissionsWindow.isDestroyed()) {
        permissionsWindow.webContents.send('permissions:state', permissionsState);
      }
      break;

    case 'tools_list':
      toolsList = (event.data && event.data.tools) || [];
      if (permissionsWindow && !permissionsWindow.isDestroyed()) {
        permissionsWindow.webContents.send('permissions:tools', toolsList);
      }
      break;

    case 'plugins_list':
      pluginsList = (event.data && event.data.plugins) || [];
      if (permissionsWindow && !permissionsWindow.isDestroyed()) {
        permissionsWindow.webContents.send('permissions:plugins', pluginsList);
      }
      break;

    case 'credentials_state':
      credentialsState = event.data || null;
      if (credentialsWindow && !credentialsWindow.isDestroyed()) {
        credentialsWindow.webContents.send('credentials:state', credentialsState);
      }
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

// ── Profile setup window ──────────────────────────────────────────────────────

function openSetupWindow() {
  if (setupWindow) { setupWindow.focus(); return; }

  setupWindow = new BrowserWindow({
    width: 380,
    height: 520,
    resizable: false,
    title: 'Felix Setup',
    backgroundColor: '#12101e',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  setupWindow.setMenuBarVisibility(false);
  setupWindow.loadFile(path.join(__dirname, 'windows', 'profile-setup.html'));
  setupWindow.on('closed', () => { setupWindow = null; });
}

function closeSetupWindow() {
  if (setupWindow) { setupWindow.close(); setupWindow = null; }
}

ipcMain.on('profile:create', (_event, data) => {
  sendToCerebral({ type: 'create_profile', data });
});

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
    backgroundColor: '#12101e',
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'windows', 'main.html'),
    hashStr ? { hash: hashStr } : undefined);
  mainWindow.on('closed', () => { mainWindow = null; });
}

// Queue popup retired in Issue #194 — the Queue lives in the Main window's
// sidebar pane now and talks directly to the Cerebral WebSocket. The tray
// menu's "Queue (N pending)" item deep-links into `main.html#queue` via
// openMainWindow('#queue'), and `pendingItems` is kept only so the tray
// icon's tooltip + count-in-title stay current (retired by the Tray
// Collapse sub-issue).

// Insights popup retired in Issue #196 — the Insights view lives in the
// Main window's sidebar pane now and talks directly to the Cerebral
// WebSocket. The tray menu's "Insights" item deep-links into
// `main.html#insights` via openMainWindow('#insights').

// Memory popup retired in Issue #198 — the Memory view lives in the Main
// window's sidebar pane now and talks directly to the Cerebral WebSocket.
// The tray menu's "Memory" item deep-links into `main.html#memory` via
// openMainWindow('#memory').

// ── Permissions window (Issue #53) ────────────────────────────────────────────

function openPermissionsWindow() {
  if (permissionsWindow && !permissionsWindow.isDestroyed()) {
    permissionsWindow.focus();
    pushPermissionsToWindow();
    return;
  }

  permissionsWindow = new BrowserWindow({
    width: 480,
    height: 560,
    resizable: true,
    title: 'Felix — Permissions',
    backgroundColor: '#12101e',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  permissionsWindow.setMenuBarVisibility(false);
  permissionsWindow.loadFile(path.join(__dirname, 'windows', 'permissions.html'));
  permissionsWindow.on('closed', () => { permissionsWindow = null; });
}

function pushPermissionsToWindow() {
  if (!permissionsWindow || permissionsWindow.isDestroyed()) return;
  if (permissionsState) {
    permissionsWindow.webContents.send('permissions:state', permissionsState);
  }
  permissionsWindow.webContents.send('permissions:tools',   toolsList);
  permissionsWindow.webContents.send('permissions:plugins', pluginsList);
}

// Renderer just finished loading: send what we have and ask Cerebral
// to refresh, in case the snapshot is stale (e.g. profile switched
// while the window was closed).
ipcMain.on('permissions:ready', (event) => {
  if (!permissionsWindow || permissionsWindow.isDestroyed()) return;
  if (event.sender !== permissionsWindow.webContents) return;
  pushPermissionsToWindow();
  sendToCerebral({ type: 'list_permissions' });
  sendToCerebral({ type: 'list_tools' });
  sendToCerebral({ type: 'list_plugins' });
});

// Outbound from the Permissions renderer — the store wraps every user
// action in a single envelope so the main process is just a thin
// forwarder.
ipcMain.on('permissions:send', (_event, envelope) => {
  if (envelope && typeof envelope === 'object' && envelope.type) {
    sendToCerebral(envelope);
  }
});

// ── Credentials window (Issue #114) ───────────────────────────────────────────
//
// Per-active-profile connected-account status from the #112 store + a
// Connect-Google action driving #113's OAuth flow. Renderer talks
// ipcRenderer directly and main.js is a thin forwarder (no lib store —
// there is no client-side state resolution to unit-test, unlike
// Permissions). Pending its own Slice 2 migration under #191.

function openCredentialsWindow() {
  if (credentialsWindow && !credentialsWindow.isDestroyed()) {
    credentialsWindow.focus();
    if (credentialsState) {
      credentialsWindow.webContents.send('credentials:state', credentialsState);
    }
    return;
  }

  credentialsWindow = new BrowserWindow({
    width: 380,
    height: 460,
    resizable: false,
    title: 'Felix — Credentials',
    backgroundColor: '#12101e',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  credentialsWindow.setMenuBarVisibility(false);
  credentialsWindow.loadFile(path.join(__dirname, 'windows', 'credentials.html'));
  credentialsWindow.on('closed', () => { credentialsWindow = null; });
}

ipcMain.on('credentials:request', (e) => {
  if (credentialsState) e.sender.send('credentials:state', credentialsState);
  sendToCerebral({ type: 'list_credentials' });
});
ipcMain.on('credentials:set-client', (_e, { client_id, client_secret }) =>
  sendToCerebral({
    type: 'set_credential_client',
    data: { client_id, client_secret },
  })
);
ipcMain.on('credentials:connect',    () => sendToCerebral({ type: 'connect_google' }));
ipcMain.on('credentials:disconnect', () => sendToCerebral({ type: 'disconnect_credential' }));

// Issue #148 — static-token settings UI. Two new channels for the API-keys
// section (one row per static-token plugin). Write-only contract: the
// renderer NEVER receives the token value back, only {status, source}.
ipcMain.on('credentials:set-static-token', (_e, { provider, value }) =>
  sendToCerebral({
    type: 'set_static_token',
    data: { provider, value },
  })
);
ipcMain.on('credentials:clear-static-token', (_e, { provider }) =>
  sendToCerebral({
    type: 'clear_static_token',
    data: { provider },
  })
);

// ── Consent prompt window (Issue #48) ─────────────────────────────────────────
//
// One BrowserWindow per outstanding ASK. The ConsentManager owns the
// pending-request map; this function just renders the UI and forwards
// the user's choice back to the manager.

function openConsentWindow(record) {
  // Reuse if a window for this request_id already exists (shouldn't
  // happen — the manager keys by request_id and ignores duplicates —
  // but guard anyway so a refresh from Cerebral doesn't spawn a clone).
  const existing = consentWindows.get(record.request_id);
  if (existing && !existing.isDestroyed()) {
    existing.webContents.send('consent:show', record);
    existing.focus();
    return;
  }

  const win = new BrowserWindow({
    width:           360,
    height:          340,
    resizable:       false,
    title:           'Felix — Permission',
    backgroundColor: '#12101e',
    alwaysOnTop:     true,
    skipTaskbar:     true,
    webPreferences: {
      nodeIntegration:  true,
      contextIsolation: false,
    },
  });

  win.setMenuBarVisibility(false);
  consentWindows.set(record.request_id, win);

  win.webContents.once('did-finish-load', () => {
    win.webContents.send('consent:show', record);
  });
  win.loadFile(path.join(__dirname, 'windows', 'consent.html'));

  win.on('closed', () => {
    consentWindows.delete(record.request_id);
    // If the window was closed externally (user hit the OS close button)
    // and we hadn't already received a choice, treat it as a deny so
    // the manager cleans up and Cerebral gets a response promptly.
    if (consentManager.get(record.request_id)) {
      consentManager.respond(record.request_id, 'deny');
    }
  });
}

function closeConsentWindow(request_id) {
  const win = consentWindows.get(request_id);
  if (win && !win.isDestroyed()) {
    consentWindows.delete(request_id);
    win.close();
  }
}

ipcMain.on('consent:choose', (_event, { request_id, choice }) => {
  consentManager.respond(request_id, choice);
});

ipcMain.on('consent:ready', (event) => {
  // A renderer just finished loading. Find which window the event came
  // from and re-send the payload, in case the ipc race lost the first one.
  for (const [request_id, win] of consentWindows.entries()) {
    if (!win.isDestroyed() && win.webContents === event.sender) {
      const record = consentManager.get(request_id);
      if (record) win.webContents.send('consent:show', record);
      return;
    }
  }
});

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

  template.push({ label: 'Felix', enabled: false });

  if (!isConnected) {
    template.push({ label: '● Connecting...', enabled: false });
  } else if (felixState === 'active') {
    template.push({ label: '● ACTIVE — listening', enabled: false });
  } else {
    template.push({ label: '● Running', enabled: false });
  }

  template.push({ type: 'separator' });

  if (isConnected) {
    template.push({ label: 'Open Felix', click: () => openMainWindow() });
    const count      = pendingItems.length;
    const queueLabel = count > 0 ? `Queue  (${count} pending)` : 'Queue';
    template.push({ label: queueLabel, click: () => openMainWindow('#queue') });
    template.push({
      label: visState.visible ? 'Hide Visualiser' : 'Show Visualiser',
      click: toggleVisualiser,
    });
    template.push({ label: 'Insights', click: () => openMainWindow('#insights') });
    template.push({ label: 'Memory', click: () => openMainWindow('#memory') });
    template.push({ label: 'Permissions', click: openPermissionsWindow });
    template.push({ label: 'Credentials', click: openCredentialsWindow });

    // ── Model ─────────────────────────────────────────────────────────────────
    const modelLabel = activeModel
      ? `Model: ${activeIsCloud ? '☁ ' : '◉ '}${activeModel}`
      : 'Model: —';
    template.push({
      label: modelLabel,
      submenu: buildModelSubmenu({
        models:        modelsList,
        active:        activeModel,
        last:          lastModel,
        activeIsCloud,
        taskModels,
        taskTypes:     ['chat', 'extraction'],
        onSwitchModel: (id) => sendToCerebral({ type: 'switch_model', data: { model_id: id } }),
        onSetTaskModel: (taskType, modelId) =>
          sendToCerebral({ type: 'set_task_model', data: { task_type: taskType, model_id: modelId } }),
        onRefresh:     () => sendToCerebral({ type: 'refresh_models' }),
      }),
    });

    // ── Notifications ─────────────────────────────────────────────────────────
    const notifOn = notifManager.enabled;
    template.push({
      label: `Notifications: ${notifOn ? 'On' : 'Off'}`,
      click: () => {
        notifManager.setEnabled(!notifOn);
        refreshMenu();
      },
    });

    if (notifOn) {
      const currentInterval = notifManager.intervalMinutes;
      const intervalPresets = [
        { label: 'Reminder off',    minutes: 0   },
        { label: 'Every 30 min',   minutes: 30  },
        { label: 'Every 1 hour',   minutes: 60  },
        { label: 'Every 2 hours',  minutes: 120 },
        { label: 'Every 4 hours',  minutes: 240 },
      ];
      template.push({
        label: 'Reminder interval',
        submenu: intervalPresets.map(({ label, minutes }) => ({
          label,
          type:    'radio',
          checked: currentInterval === minutes,
          click:   () => {
            notifManager.setIntervalMinutes(minutes);
            refreshMenu();
          },
        })),
      });
    }

    // ── Camera ────────────────────────────────────────────────────────────────
    const cameraOn = settingsStore.get('camera_enabled');
    template.push({
      label: `Camera: ${cameraOn ? 'On' : 'Off'}`,
      click: () => {
        const next = !cameraOn;
        settingsStore.set('camera_enabled', next);
        sendToCerebral({ type: 'set_camera_enabled', data: { enabled: next } });
        refreshMenu();
      },
    });

    template.push({ type: 'separator' });
  }

  if (activeProfile) {
    template.push({ label: `Profile: ${activeProfile.name}`, enabled: false });

    if (allProfiles.length > 1) {
      const switchItems = allProfiles.map((p) => ({
        label:   p.name,
        type:    'radio',
        checked: p.id === activeProfile.id,
        click:   () => sendToCerebral({ type: 'switch_profile', data: { id: p.id } }),
      }));
      template.push({ label: 'Switch profile', submenu: switchItems });
    }

    template.push({ label: 'New profile...', click: openSetupWindow });
  }

  template.push({ type: 'separator' });
  template.push({ label: 'Quit', click: quit });

  return Menu.buildFromTemplate(template);
}

function refreshMenu() {
  if (!tray) return;

  const count = pendingItems.length;
  const tooltip = !isConnected
    ? 'Felix — connecting...'
    : felixState === 'active'
      ? 'Felix — ACTIVE'
      : count > 0
        ? `Felix — ${count} pending action${count === 1 ? '' : 's'}`
        : activeProfile
          ? `Felix — ${activeProfile.name}`
          : 'Felix — running';

  tray.setToolTip(tooltip);
  tray.setContextMenu(buildMenu());

  if (tray.setTitle) {
    tray.setTitle(count > 0 ? ` ${count}` : '');
  }
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

app.whenReady().then(() => {
  if (app.dock) app.dock.hide();
  app.setName('Felix');

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
