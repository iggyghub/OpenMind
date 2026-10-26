'use strict';

// Tray main-process half of mark-kit screenshots (see mark-kit-host.js).
// Captures a rect of the Main window's OWN webContents -- it cannot see any
// other app, so this is not ADR-0005 screen_capture; writing the image to disk
// still goes through the gated marks_add tool.

function sanitizeRect(rect) {
  if (!rect || typeof rect !== 'object') return null;
  var r = {};
  for (var k of ['x', 'y', 'width', 'height']) {
    var v = Math.round(Number(rect[k]));
    if (!Number.isFinite(v) || v < 0 || v > 20000) return null;
    r[k] = v;
  }
  return r.width > 0 && r.height > 0 ? r : null;
}

// -> png data URL, or null on a bad rect / capture failure.
async function captureMark(webContents, rect) {
  var r = sanitizeRect(rect);
  if (!r || !webContents) return null;
  try {
    var img = await webContents.capturePage(r);
    return img.isEmpty() ? null : img.toDataURL();
  } catch (_e) {
    return null;
  }
}

module.exports = { sanitizeRect, captureMark };
