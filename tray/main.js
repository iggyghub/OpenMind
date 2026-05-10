const { app, Tray, Menu, BrowserWindow, Notification, nativeImage, ipcMain, screen } = require('electron');
const WebSocket = require('ws');
const path = require('path');
const { VisualiserState }      = require('./lib/visualiser-state');
const { PositionStore }        = require('./lib/position-store');
const { SettingsStore }        = require('./lib/settings-store');
const { NotificationManager }  = require('./lib/notification-manager');
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
let queueWindow      = null;
let visualiserWindow = null;
let insightsWindow   = null;
let insightsList     = [];
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
  onNotificationClick:  () => openQueueWindow(),
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
      pendingItems = (event.data && event.data.items) || [];
      notifManager.handleQueueUpdate(pendingItems);
      refreshMenu();
      if (queueWindow && !queueWindow.isDestroyed()) {
        queueWindow.webContents.send('queue:items', pendingItems);
      }
      break;

    case 'queue_item_result':
      if (queueWindow && !queueWindow.isDestroyed()) {
        queueWindow.webContents.send('queue:item-result', event.data);
      }
      break;

    case 'insights_update':
      insightsList = (event.data && event.data.insights) || [];
      if (insightsWindow && !insightsWindow.isDestroyed()) {
        insightsWindow.webContents.send('insights:data', insightsList);
      }
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

// ── Queue window ──────────────────────────────────────────────────────────────

function openQueueWindow() {
  if (queueWindow && !queueWindow.isDestroyed()) {
    queueWindow.focus();
    queueWindow.webContents.send('queue:items', pendingItems);
    return;
  }

  queueWindow = new BrowserWindow({
    width: 340,
    height: 480,
    resizable: false,
    title: 'Felix — Queue',
    backgroundColor: '#12101e',
    ...queueWindowPosition(),
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  queueWindow.setMenuBarVisibility(false);
  queueWindow.loadFile(path.join(__dirname, 'windows', 'queue.html'));
  queueWindow.on('closed', () => { queueWindow = null; });
}

function queueWindowPosition() {
  if (!tray) return {};
  try {
    const { x, y, width, height } = tray.getBounds();
    const winW = 340, winH = 480;
    const posX = Math.round(x + width / 2 - winW / 2);
    const posY = process.platform === 'darwin'
      ? Math.round(y + height + 4)
      : Math.round(y - winH - 4);
    return { x: posX, y: posY };
  } catch { return {}; }
}

ipcMain.on('queue:approve', (_e, item_id) => sendToCerebral({ type: 'approve_item', data: { item_id } }));
ipcMain.on('queue:dismiss', (_e, item_id) => sendToCerebral({ type: 'dismiss_item', data: { item_id } }));
ipcMain.on('queue:clear', () => {
  pendingItems.forEach(item => sendToCerebral({ type: 'dismiss_item', data: { item_id: item.id } }));
});
ipcMain.on('queue:request', (e) => {
  e.sender.send('queue:items', pendingItems);
  sendToCerebral({ type: 'list_queue' });
});

// ── Insights window ───────────────────────────────────────────────────────────

function openInsightsWindow() {
  if (insightsWindow && !insightsWindow.isDestroyed()) {
    insightsWindow.focus();
    insightsWindow.webContents.send('insights:data', insightsList);
    return;
  }

  insightsWindow = new BrowserWindow({
    width: 360,
    height: 500,
    resizable: false,
    title: 'Felix — Insights',
    backgroundColor: '#12101e',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  insightsWindow.setMenuBarVisibility(false);
  insightsWindow.loadFile(path.join(__dirname, 'windows', 'insights.html'));
  insightsWindow.on('closed', () => { insightsWindow = null; });
}

ipcMain.on('insights:request', (e) => {
  e.sender.send('insights:data', insightsList);
  sendToCerebral({ type: 'list_insights' });
});
ipcMain.on('insights:pin',    (_e, id) => sendToCerebral({ type: 'pin_insight',    data: { insight_id: id } }));
ipcMain.on('insights:delete', (_e, id) => sendToCerebral({ type: 'delete_insight', data: { insight_id: id } }));
ipcMain.on('insights:edit',   (_e, { id, description }) =>
  sendToCerebral({ type: 'edit_insight', data: { insight_id: id, description } })
);

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
    const count      = pendingItems.length;
    const queueLabel = count > 0 ? `Queue  (${count} pending)` : 'Queue';
    template.push({ label: queueLabel, click: openQueueWindow });
    template.push({
      label: visState.visible ? 'Hide Visualiser' : 'Show Visualiser',
      click: toggleVisualiser,
    });
    template.push({ label: 'Insights', click: openInsightsWindow });

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

  tray.on('click', openQueueWindow);

  refreshMenu();
  connectToCerebral();
  console.log('[tray] Felix tray started');
});

app.on('window-all-closed', () => {
  // Keep alive as tray-only app
});

app.on('before-quit', () => {
  isQuitting = true;
});
