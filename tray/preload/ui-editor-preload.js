'use strict';

// Preload for the UI Editor window ONLY (see main.js's setWindowOpenHandler,
// UI_EDITOR_ORIGIN branch). Exposes native Save/Open dialogs so "New page"
// and "Local file" can hand back a real filesystem path -- something a
// plain webpage can't get out of a save dialog on its own for security
// reasons. editor.html checks for window.felixDialogs before using this;
// the tool opened standalone in a regular browser tab (per its own README)
// never gets this preload and falls back to its existing text-input path
// field there.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('felixDialogs', {
  saveNewPagePath:  () => ipcRenderer.invoke('ui-editor:save-dialog'),
  openLocalFilePath: () => ipcRenderer.invoke('ui-editor:open-dialog'),
});
