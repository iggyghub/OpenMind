const { app, Tray, Menu, BrowserWindow, Notification, nativeImage, ipcMain, screen, shell, globalShortcut, dialog } = require('electron');
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
// SD-3 (#556) -- boot self-check state
const DATA_DIR = path.join(__dirname, '..', 'cerebral', 'data');

let tray = null;
let ws = null;
let isConnected = false;
let isQuitting  = false;
// #817 -- true from the moment a relaunch is requested until this process
// exits. A second restart/rollback request arriving before app.quit() has
// actually torn this process down (e.g. two campaign slices auto-merging
// back to back) must not fire a second app.relaunch() -- that's what left
// two Cerebral processes fighting over :7766 and a stuck Electron
// relauncher helper in the same incident.
let _restartInProgress = false;
// #813/#812 follow-on -- master advancing via an external merge (e.g. a PR
// merged directly on GitHub/gh, not through self_dev's own _load()) never
// notified the running process before; it just kept running the old code
// until someone manually restarted. See _checkForMasterUpdate below.
let _bootSha = null;
let _pendingUpdateRestart = false;
const AUTO_UPDATE_POLL_MS = 5 * 60 * 1000;
let reconnectTimer = null;
// SD-3 (#556) -- set true when --felix-self-dev-boot is in argv; cleared once
// health_check is sent on the first Cerebral connection after that boot.
let _selfDevBootPending  = false;
// Resolve/reject for the pending health-check Promise (wired into runSelfCheck).
let _healthCheckResolve = null;
let _healthCheckTimer   = null;
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
  video_batch_hotkey:        'CommandOrControl+Alt+P',  // S13 #664 pause/resume
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
    // SD-3 (#556): first connection after a self-dev boot -- run the self-check.
    if (_selfDevBootPending) {
      _selfDevBootPending = false;
      ws.send(JSON.stringify({ type: 'health_check' }));
    }
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

// ── Push-to-talk global hotkey ────────────────────────────────────────────
// Registered only while mic_mode === 'ptt'. Pressing it tells Cerebral to
// start a capture with no wake word (see main.py 'ptt'). Re-applied whenever
// settings change so a rebind takes effect live.
let pttRegisteredKey = null;
function applyPTTHotkey() {
  const wantKey = settingsCache.mic_mode === 'ptt' ? settingsCache.ptt_key : null;
  if (wantKey === pttRegisteredKey) return;
  if (pttRegisteredKey) {
    try { globalShortcut.unregister(pttRegisteredKey); } catch (_) {}
    pttRegisteredKey = null;
  }
  if (wantKey) {
    try {
      const ok = globalShortcut.register(wantKey, () => sendToCerebral({ type: 'ptt' }));
      if (ok) {
        pttRegisteredKey = wantKey;
        console.log('[tray] PTT hotkey registered:', wantKey);
      } else {
        console.warn('[tray] PTT hotkey registration failed (already in use?):', wantKey);
        electronNotify('Push-to-talk', `Couldn't register "${wantKey}" — it may be in use by another app. Pick a different key.`);
      }
    } catch (e) {
      console.warn('[tray] PTT hotkey invalid:', wantKey, e.message);
    }
  }
}

// ── Video batch pause/resume global hotkey (S13 #664) ─────────────────────
// Registered always (default Ctrl+Alt+P, overridable via settings). Pressing it
// toggles the channel batch: pause if running, resume (no re-enumerate) if not.
// Global so it works while the user is in another app -- their screen is busy.
let videoHotkeyRegistered = null;
function applyVideoHotkey() {
  const wantKey = settingsCache.video_batch_hotkey || 'CommandOrControl+Alt+P';
  if (wantKey === videoHotkeyRegistered) return;
  if (videoHotkeyRegistered) {
    try { globalShortcut.unregister(videoHotkeyRegistered); } catch (_) {}
    videoHotkeyRegistered = null;
  }
  if (!wantKey) return;
  try {
    const ok = globalShortcut.register(wantKey, () => sendToCerebral({ type: 'video_batch_toggle' }));
    if (ok) {
      videoHotkeyRegistered = wantKey;
      console.log('[tray] Video batch hotkey registered:', wantKey);
    } else {
      console.warn('[tray] Video batch hotkey registration failed (already in use?):', wantKey);
    }
  } catch (e) {
    console.warn('[tray] Video batch hotkey invalid:', wantKey, e.message);
  }
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
      // #817 -- an auto-update was waiting for Felix to go idle before
      // restarting so it didn't cut off an in-progress response.
      if (_pendingUpdateRestart) {
        _pendingUpdateRestart = false;
        restartFelixSelfDev();
      }
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

    case 'video_batch_toggle': {  // S13 #664 -- hotkey feedback
      const d = event.data || {};
      if (d.action === 'paused') {
        electronNotify('Video batch', 'Paused — press the hotkey again to resume.');
      } else if (d.action === 'resumed') {
        electronNotify('Video batch',
          d.status === 'no_channel'
            ? 'Nothing to resume — start a channel batch first.'
            : 'Resumed.');
      }
      break;
    }

    case 'settings_updated': {
      const prev = settingsCache;
      settingsCache = event.data || {};
      notifManager.applySettings(settingsCache);
      // (Re)apply the PTT hotkey — covers mode switches and live rebinds.
      applyPTTHotkey();
      applyVideoHotkey();  // S13 #664 -- video batch pause/resume hotkey
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

    case 'health_ok': {
      // SD-3 (#556): response to our health_check probe after a self-dev boot.
      if (_healthCheckTimer) { clearTimeout(_healthCheckTimer); _healthCheckTimer = null; }
      if (_healthCheckResolve) {
        const d = event.data || {};
        _healthCheckResolve({ ok: true, gate_present: !!d.gate_present });
        _healthCheckResolve = null;
      }
      break;
    }

    case 'restart_felix': {
      const { isCodeLoad } = require('./lib/boot-check');
      const d = event.data || {};
      if (isCodeLoad(d.reason)) {
        restartFelixSelfDev();
      } else {
        restartFelix();
      }
      break;
    }

    case 'self_dev_manual_rollback':
      // #813 -- Cerebral broadcasts this when the self_dev_rollback tool is
      // called (chat-reachable: "tell Felix to roll back"). The actual
      // reset+relaunch still happens here in the tray layer, same reasoning
      // as the automatic SD-3 rollback ("a broken brain can't rescue itself").
      manualSelfDevRollback('chat');
      break;

    case 'computer_use:driving':
      // S2 #576 (ADR-0016 (c)): render the "Felix is driving" indicator +
      // Stop control on the Visualiser. Open the window if it isn't already
      // so the user always has a visible way to hit Stop while Felix drives.
      routeDrivingToVisualiser(event);
      break;

    case 'computer_use:thumbnail':
      // S15 #609: passive frame from Felix's isolated session. Forward to the
      // Visualiser only when it's currently mounted -- no persistence, no
      // buffering. If the window is closed the frame is dropped on the floor,
      // matching the ADR-0016 sec 7 "never-persisted, ephemeral" rule.
      if (visualiserWindow && !visualiserWindow.isDestroyed()) {
        visualiserWindow.webContents.send('visualiser:thumbnail', (event.data || {}).frame_b64);
      }
      break;

    case 'computer_use:taken_over':
      // S15 #609: server-authoritative flip of the Take over/Release state.
      if (visualiserWindow && !visualiserWindow.isDestroyed()) {
        visualiserWindow.webContents.send('visualiser:taken-over', !!(event.data || {}).taken_over);
      }
      break;
  }
}

function routeDrivingToVisualiser(event) {
  const { driving, changed, mode, windowTitle, action } = visState.handleEvent(event);
  if (!changed) return;
  // The kill-switch Stop must always be reachable while driving -- if the
  // Visualiser was hidden, open it now (not persisted; a routine Stop leg,
  // not a preference change).
  if (driving && (!visualiserWindow || visualiserWindow.isDestroyed())) {
    openVisualiserWindow();
  }
  if (visualiserWindow && !visualiserWindow.isDestroyed()) {
    // While driving, the Visualiser must accept mouse input on its Stop
    // button (default is click-through). Restored when driving flips off.
    visualiserWindow.setIgnoreMouseEvents(!driving);
    // #594 (ADR-0016 amendment f): forward the mode-aware payload so the
    // renderer can show "Felix is acting in <window> (background)" vs the
    // foreground cursor-in-use urgency -- same broadcast, real-time flip on
    // a #593 soft trip.
    visualiserWindow.webContents.send('visualiser:driving', { driving, mode, windowTitle, action });
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
  //
  // UI Editor nav button (tools/ui-editor) additionally whitelists exactly
  // http://localhost:4545/ -- the tool's own dev server, started separately
  // (`node tools/ui-editor/server.js`), never bundled into or spawned by
  // Felix itself. Hardcoded origin, not a general localhost/http allowance.
  const UI_EDITOR_ORIGIN = 'http://localhost:4545';
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    let parsed;
    try { parsed = new URL(url); } catch (_) { return { action: 'deny' }; }

    if (parsed.protocol === 'file:'
        && parsed.pathname.replace(/\\/g, '/').endsWith('/tray/windows/detached-panel.html')) {
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
    }

    if (parsed.origin === UI_EDITOR_ORIGIN) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          width:           1100,
          height:          760,
          minWidth:        480,
          minHeight:       360,
          title:           'Felix — UI Editor',
          icon:            ICO_PATH,
          backgroundColor: '#111111',
          webPreferences: {
            nodeIntegration:  false,
            contextIsolation: true,
            preload:          path.join(__dirname, 'preload', 'ui-editor-preload.js'),
          },
        },
      };
    }

    return { action: 'deny' };
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

// S2 #576 (ADR-0016 (c)): the Visualiser's Stop control fires this from the
// renderer. Forward it to Cerebral so plugins/computer_use.py can abort.
ipcMain.on('computer-use:stop', () => {
  sendToCerebral({ type: 'computer_use_stop' });
});

// S15 #609: Take-over / Release from the Visualiser. Cerebral broadcasts
// computer_use:taken_over on both flips so the button label re-syncs from
// server-authoritative state (no local optimistic toggling).
ipcMain.on('computer-use:take-over', () => {
  sendToCerebral({ type: 'computer_use_take_over' });
});
ipcMain.on('computer-use:release', () => {
  sendToCerebral({ type: 'computer_use_release' });
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

// Native Save/Open dialogs for the UI Editor's "New page" and "Local file"
// fields (see setWindowOpenHandler's UI_EDITOR_ORIGIN override, which is the
// only window carrying preload/ui-editor-preload.js — a plain webpage has no
// way to get a real filesystem path back from a save dialog, so this only
// exists for the tool opened inside Felix's own Electron shell; standalone
// browser usage per the tool's own README keeps the plain text-input path).
// tools/ui-editor/server.js only ever serves/writes files under the repo
// root anyway (checkLocalPath), so a pick outside it is rejected here with
// the same constraint the server would enforce a step later, rather than
// let the user pick something that 403s after the fact.
const { relativeToRoot } = require('./lib/repo-path');
const UI_EDITOR_ROOT = path.join(__dirname, '..');

ipcMain.handle('ui-editor:save-dialog', async () => {
  const result = await dialog.showSaveDialog({
    title: 'New page',
    defaultPath: UI_EDITOR_ROOT,
    filters: [{ name: 'HTML', extensions: ['html', 'htm'] }],
  });
  if (result.canceled || !result.filePath) return { canceled: true };
  const rel = relativeToRoot(UI_EDITOR_ROOT, result.filePath);
  if (!rel) return { canceled: false, error: 'Must be inside the OpenMind folder.' };
  return { canceled: false, path: rel };
});

ipcMain.handle('ui-editor:open-dialog', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Open local file',
    defaultPath: UI_EDITOR_ROOT,
    properties: ['openFile'],
    filters: [{ name: 'HTML', extensions: ['html', 'htm'] }, { name: 'All files', extensions: ['*'] }],
  });
  if (result.canceled || !result.filePaths[0]) return { canceled: true };
  const rel = relativeToRoot(UI_EDITOR_ROOT, result.filePaths[0]);
  if (!rel) return { canceled: false, error: 'Must be inside the OpenMind folder.' };
  return { canceled: false, path: rel };
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

  // Click-through: mouse events pass to whatever is underneath -- unless
  // computer_use is currently driving, in which case the Stop button must be
  // clickable. S2 #576 sets this back to true when driving flips off.
  visualiserWindow.setIgnoreMouseEvents(!visState.driving);
  visualiserWindow.setMenuBarVisibility(false);
  visualiserWindow.loadFile(path.join(__dirname, 'windows', 'visualiser.html'));

  // Sync current state immediately after load
  visualiserWindow.webContents.once('did-finish-load', () => {
    visualiserWindow.webContents.send('visualiser:state', visState.state);
    // S2 #576: sync the (c) Felix-is-driving overlay on window open so the
    // Stop control is present the moment the window mounts. #594: include
    // the mode-aware fields so a mid-flight open (e.g. after a crash
    // recovery) shows the correct background/foreground phrasing.
    visualiserWindow.webContents.send('visualiser:driving', {
      driving: visState.driving, mode: visState.mode,
      windowTitle: visState.windowTitle, action: visState.action,
    });
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
  // #813 -- manual companion to the automatic SD-3 boot-check rollback.
  // Confirm first: git reset --hard discards anything since last_known_good.
  template.push({
    label: 'Roll back last self-dev change',
    click: () => {
      const response = dialog.showMessageBoxSync(null, {
        type: 'warning',
        buttons: ['Cancel', 'Roll back'],
        defaultId: 0,
        cancelId: 0,
        message: 'Roll back Felix to its last known-good self-dev state?',
        detail: 'This resets the live code to the last verified-good commit and restores the matching database/settings snapshot, then relaunches.',
      });
      if (response === 1) manualSelfDevRollback('tray-menu');
    },
  });
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

// #817 -- single choke point for every app.relaunch()+quit() in this file.
// Refuses a second relaunch while one is already in flight (see
// _restartInProgress above). Returns true if it actually relaunched.
function _relaunch(extraFlags, source) {
  if (_restartInProgress) {
    trayLog(`restart: ignored duplicate relaunch request from '${source}' -- one is already in flight`);
    return false;
  }
  _restartInProgress = true;
  const { cleanFelixArgv } = require('./lib/boot-check');
  trayLog(`restart: relaunching (${source}) via app.relaunch() with [${extraFlags.join(', ')}]`);
  app.relaunch({ args: cleanFelixArgv(process.argv.slice(1), extraFlags) });
  quit(); // sends Cerebral the shutdown event, then app.quit()
  return true;
}

function restartFelix() {
  _relaunch(['--felix-restart'], 'restartFelix');
}

// SD-3 (#556): self-dev restart -- pin the current master SHA + snapshot
// openmind.db + felix-settings.json before handing off to the new code.
// On the relaunched boot, runSelfDevCheck() verifies the new code is healthy
// before promoting; on failure it auto-reverts and relaunches again.
function restartFelixSelfDev() {
  // #817 -- bail before pinning/snapshotting too, not just before the
  // relaunch call: two campaign slices auto-merging back to back must not
  // let the second call's pin overwrite the first call's in-flight state.
  if (_restartInProgress) {
    trayLog("restart: ignored duplicate self-dev restart request -- one is already in flight");
    return;
  }

  const fs               = require('fs');
  const { execFileSync } = require('child_process');
  const gitExe           = 'git';
  const repoRoot         = path.join(__dirname, '..');
  const bootCheck        = require('./lib/boot-check');

  try {
    bootCheck.pinAndSnapshot({
      dataDir:        DATA_DIR,
      gitRevParseFn:  () => execFileSync(gitExe, ['rev-parse', 'HEAD'],
        { cwd: repoRoot, encoding: 'utf8' }).trim(),
      copyFileFn:     (src, dest) => fs.copyFileSync(src, dest),
      mkdirFn:        (dir) => fs.mkdirSync(dir, { recursive: true }),
      readDirFn:      (dir) => { try { return fs.readdirSync(dir); } catch (_) { return []; } },
      removeDirFn:    (dir) => fs.rmSync(dir, { recursive: true, force: true }),
      writeFileFn:    (p, d) => fs.writeFileSync(p, d, 'utf8'),
    });
    trayLog('SD-3: pinned SHA + snapshotted state before self-dev restart');
  } catch (e) {
    trayLog(`SD-3: pin/snapshot failed (continuing restart): ${e}`);
  }

  _relaunch(['--felix-restart', '--felix-self-dev-boot'], 'restartFelixSelfDev');
}

// SD-3 (#556): called on boot when --felix-self-dev-boot is in argv.
// Wires runSelfCheck to the pending WS health_check flow.
function runSelfDevCheck() {
  const fs               = require('fs');
  const { execFileSync } = require('child_process');
  const gitExe           = 'git';
  const repoRoot         = path.join(__dirname, '..');
  const { runSelfCheck, CHECK_TIMEOUT_MS } = require('./lib/boot-check');

  runSelfCheck({
    dataDir:     DATA_DIR,
    readFileFn:  (p) => { try { return fs.readFileSync(p, 'utf8'); } catch (_) { return null; } },
    writeFileFn: (p, d) => fs.writeFileSync(p, d, 'utf8'),
    copyFileFn:  (src, dest) => fs.copyFileSync(src, dest),
    gitResetFn:  (sha) => execFileSync(gitExe, ['reset', '--hard', sha],
      { cwd: repoRoot, stdio: 'ignore' }),
    notifyFn: (msg) => {
      trayLog(`SD-3: ${msg}`);
      electronNotify('Felix — boot check', msg);
    },
    relauncher: () => {
      // Relaunch without --felix-self-dev-boot so the old code boots normally
      _relaunch(['--felix-restart'], 'runSelfDevCheck-rollback');
    },
    checkFn: () => new Promise((resolve, reject) => {
      _healthCheckResolve = resolve;
      _healthCheckTimer   = setTimeout(() => {
        _healthCheckResolve = null;
        _healthCheckTimer   = null;
        reject(new Error(`health_check timeout after ${CHECK_TIMEOUT_MS}ms`));
      }, CHECK_TIMEOUT_MS);
    }),
  }).then(res => {
    trayLog(`SD-3: self-check result: ${(res && res.result) || 'no-op'}`);
  }).catch(e => {
    trayLog(`SD-3: self-check error: ${e}`);
  });
}

// #813 -- on-demand rollback to the last self-dev snapshot, independent of
// the automatic pending-boot-check path above: reachable from the tray menu
// (source is unresponsive, or the user just wants it back) and from Felix
// via a chat-triggered WS message (case 'self_dev_manual_rollback' below).
// Reuses the exact fs/git wiring runSelfDevCheck() already uses.
function manualSelfDevRollback(source) {
  const fs               = require('fs');
  const { execFileSync } = require('child_process');
  const gitExe           = 'git';
  const repoRoot         = path.join(__dirname, '..');
  const { manualRollback } = require('./lib/boot-check');

  return manualRollback({
    dataDir:    DATA_DIR,
    readFileFn: (p) => { try { return fs.readFileSync(p, 'utf8'); } catch (_) { return null; } },
    copyFileFn: (src, dest) => fs.copyFileSync(src, dest),
    gitResetFn: (sha) => execFileSync(gitExe, ['reset', '--hard', sha],
      { cwd: repoRoot, stdio: 'ignore' }),
    notifyFn: (msg) => {
      trayLog(`manual rollback (${source}): ${msg}`);
      electronNotify('Felix — manual rollback', msg);
    },
    relauncher: () => {
      _relaunch(['--felix-restart'], `manualSelfDevRollback-${source}`);
    },
  }).then(res => {
    if (!res.ok) trayLog(`manual rollback (${source}): nothing to roll back -- ${res.reason}`);
    return res;
  });
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
      // No detached: PS 5.1 under DETACHED_PROCESS exits 0 without running
      // the -File script on this box (#519). The parent is the long-lived
      // relaunched tray, so nothing needs detaching anyway.
      { stdio: 'ignore', windowsHide: true },
    );
    child.on('error', (err) => trayLog(`cerebral respawn error: ${err}`));
    child.unref();
    trayLog(`restart: relaunched tray up, spawned launcher for Cerebral (pid ${child.pid})`);
  } catch (e) {
    trayLog(`cerebral respawn threw: ${e}`);
  }
}

// #817 -- notice when master has advanced beyond what this process booted
// with, regardless of *how* it got there (self_dev, a PR merged directly on
// GitHub, or a manual `git pull` in a terminal), and restart to pick it up.
// Reuses restartFelixSelfDev()'s pin+snapshot+boot-self-check path -- an
// externally-merged change deserves the same rollback safety net a
// self_dev-triggered one gets, not a bare unsafe restart.
function _gitOut(args, opts) {
  const { execFileSync } = require('child_process');
  return execFileSync('git', args, {
    cwd: path.join(__dirname, '..'), encoding: 'utf8', timeout: 30_000, ...opts,
  }).trim();
}

function _checkForMasterUpdate() {
  if (_restartInProgress || _pendingUpdateRestart) return;

  const { checkForUpdate } = require('./lib/boot-check');
  const decision = checkForUpdate({
    gitFetchFn:      () => _gitOut(['fetch', '--quiet'], { stdio: 'ignore' }),
    gitRevParseFn:   (ref) => _gitOut(['rev-parse', ref]),
    gitMergeFfOnlyFn: (sha) => _gitOut(['merge', '--ff-only', sha], { stdio: 'ignore' }),
    bootSha: _bootSha,
    isIdle:  felixState === 'idle',
  });

  if (decision.action === 'skip') {
    trayLog(`auto-update: ${decision.reason}`);
    return;
  }
  if (decision.action === 'none') return;

  if (decision.action === 'restart') {
    trayLog('auto-update: new commits since boot, Felix idle -- restarting now');
    restartFelixSelfDev();
    return;
  }

  // 'defer' -- don't interrupt an in-progress response/chain; fire on the
  // next 'passive' transition instead (see the WS message switch above).
  _pendingUpdateRestart = true;
  trayLog('auto-update: new commits since boot, Felix is active -- restart deferred until idle');
}

// Without this, Felix has no way to tell "the user clicked the pinned
// taskbar icon while I'm already running (hidden, per #188's close-to-tray)"
// apart from "launch a brand new second process" -- which Windows does by
// default for any un-locked app. requestSingleInstanceLock() makes THIS
// process the sole owner; a second launch attempt (pinned icon, desktop
// shortcut, anything) fires 'second-instance' here instead of ever getting
// its own app.whenReady(), and the second process quits immediately.
const _gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!_gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => openMainWindow());
}

app.whenReady().then(() => {
  if (!_gotSingleInstanceLock) return;

  // Without an explicit AppUserModelID, Windows derives one from the launching
  // process, so the desktop/taskbar shortcut (which runs through powershell.exe
  // -> launch-felix.ps1) and this Electron window end up with different
  // identities -- two separate taskbar icons instead of one. Set here (before
  // any window exists) AND on the shortcut files themselves (System.AppUserModel.ID
  // property, set via scripts/set-shortcut-appid.ps1) so both sides match and
  // Windows merges them into a single icon.
  // NOTE: the real method name is setAppUserModelId (lowercase "d") despite
  // Electron's own docs showing it as setAppUserModelID -- the uppercase
  // spelling is `undefined` on this app object and crashed the whole tray
  // process on every launch (confirmed via an isolated test app, not just
  // this file: Object.keys(app) lists 'setAppUserModelId').
  if (process.platform === 'win32') app.setAppUserModelId('OpenMind.Felix');

  if (app.dock) app.dock.hide();
  app.setName('Felix');

  try {
    _bootSha = _gitOut(['rev-parse', 'HEAD']);
  } catch (e) {
    trayLog(`auto-update: could not capture boot SHA -- auto-update disabled this session: ${e}`);
  }
  if (_bootSha) setInterval(_checkForMasterUpdate, AUTO_UPDATE_POLL_MS);

  // Second half of "Restart Felix" (#443 rework): this instance was
  // relaunched by restartFelix(); Cerebral is down — bring it back.
  if (process.argv.includes('--felix-restart')) {
    respawnCerebral();
    // The window was open (or at least reachable) before the restart, so
    // reopening it here — instead of leaving the user back at "click the
    // tray icon to find it" — is what makes the restart feel like a
    // restart of the app they were looking at, not a silent respawn.
    openMainWindow();
  }
  // SD-3 (#556): self-dev boot -- arm the health-check before connectToCerebral().
  if (process.argv.includes('--felix-self-dev-boot')) _selfDevBootPending = true;

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
  applyVideoHotkey();  // S13 #664 -- register the default before settings arrive
  connectToCerebral();
  // SD-3 (#556): set up the self-check Promise after connectToCerebral so
  // the timeout starts only when we're actually trying to reach Cerebral.
  if (process.argv.includes('--felix-self-dev-boot')) runSelfDevCheck();
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
  try { globalShortcut.unregisterAll(); } catch (_) {}
});
