'use strict';

// Conversation attachment helpers -- S14 (#297).
//
// Pure helpers shared between the renderer (chip row + per-turn chips +
// drag-drop accept logic) and the Node test suite. Anything that needs a
// DOM lives in the renderer; this module is just data shaping.
//
// Dual-mode export: window.AttachmentChips for the renderer (no
// nodeIntegration) and module.exports for jest. Body wrapped in an IIFE
// so a plain <script src> tag doesn't collide on top-level consts.

(function () {
  // Suffix → kind table. Kept in sync with cerebral/db/attachments.py so
  // the chip the renderer draws while the upload is in flight matches the
  // kind the backend ends up storing. If they diverge the chip will
  // briefly mis-render and then snap to the backend's classification --
  // the renderer treats the backend reply as authoritative.
  var TEXT_SUFFIXES = [
    'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'log',
    'html', 'htm', 'xml', 'yaml', 'yml', 'ini', 'cfg', 'toml',
    'py', 'js', 'ts', 'jsx', 'tsx', 'css', 'sh', 'rb', 'go',
    'rs', 'java', 'kt', 'c', 'h', 'cpp', 'hpp', 'sql',
  ];
  var IMAGE_SUFFIXES = [
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'svg',
  ];

  // Hard ceiling on inline upload payloads. Anything larger is still
  // selectable (the user can switch to a Files-plugin flow later) but we
  // refuse to base64 it into the WebSocket -- a 100MB blob would freeze
  // the renderer thread during the file-read step.
  var MAX_INLINE_BYTES = 25 * 1024 * 1024;

  function suffixOf(name) {
    var s = String(name || '');
    var dot = s.lastIndexOf('.');
    if (dot < 0 || dot === s.length - 1) return '';
    return s.slice(dot + 1).toLowerCase();
  }

  function classify(name, mime) {
    var suf = suffixOf(name);
    if (TEXT_SUFFIXES.indexOf(suf) >= 0)  return 'text';
    if (suf === 'pdf' || (mime || '').toLowerCase() === 'application/pdf') return 'pdf';
    if (IMAGE_SUFFIXES.indexOf(suf) >= 0) return 'image';
    if ((mime || '').toLowerCase().indexOf('image/') === 0) return 'image';
    if ((mime || '').toLowerCase().indexOf('text/')  === 0) return 'text';
    return 'binary';
  }

  // Tiny glyph for the chip's left edge. Pure aesthetics -- the kind is
  // also reflected as a data attribute the CSS can hook into.
  function iconFor(kind) {
    if (kind === 'text')   return '¶';   // pilcrow
    if (kind === 'pdf')    return '⧉';   // two joined squares-ish
    if (kind === 'image')  return '▣';   // square with inner square
    return '●';                          // filled circle
  }

  // Compact size formatter so a chip says "12 KB" instead of "12345 B".
  // Skips a decimal for sub-kilobyte sizes (5 B not 5.0 B). Negative or
  // NaN treats as zero so a future signed-int regression doesn't crash.
  function formatSize(bytes) {
    var n = Number(bytes);
    if (!isFinite(n) || n < 0) n = 0;
    if (n < 1024) return n + ' B';
    var kib = n / 1024;
    if (kib < 1024) return (kib >= 100 ? Math.round(kib) : kib.toFixed(1)) + ' KB';
    var mib = kib / 1024;
    return (mib >= 100 ? Math.round(mib) : mib.toFixed(1)) + ' MB';
  }

  // Tolerable upload? Currently just a size check, but isolated here so a
  // future MIME allowlist can land in one place.
  function isAcceptable(file) {
    if (!file || typeof file.size !== 'number') return false;
    return file.size <= MAX_INLINE_BYTES;
  }

  // Normalise an attachment record (either renderer-pending or backend-
  // returned) into the shape the chip helpers expect. Keys are stable.
  function normalise(att) {
    var src = att || {};
    var name = src.filename || src.name || 'upload';
    var mime = src.mime || src.type || '';
    var kind = src.kind || classify(name, mime);
    var size = (typeof src.size === 'number') ? src.size : 0;
    return {
      id:       (typeof src.id !== 'undefined') ? src.id : null,
      filename: name,
      mime:     mime,
      size:     size,
      kind:     kind,
      has_text: !!src.has_text,
      icon:     iconFor(kind),
      size_label: formatSize(size),
    };
  }

  // Build a chip element. The renderer passes a `document` so the helper
  // works inside the Main window AND under jsdom/node-html-parser if a
  // future test wants to assert chip DOM shape. Returns the chip element.
  // ``onRemove`` is optional; when omitted the chip is non-dismissable
  // (e.g. when it's bound to an already-sent turn).
  function buildChip(doc, att, onRemove) {
    var d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d) throw new Error('attachment-chips: no document');
    var rec = normalise(att);
    var el = d.createElement('span');
    el.className = 'att-chip';
    el.setAttribute('data-att-id', String(rec.id == null ? '' : rec.id));
    el.setAttribute('data-att-kind', rec.kind);
    var ico = d.createElement('span');
    ico.className = 'att-chip-ico';
    ico.textContent = rec.icon;
    el.appendChild(ico);
    var name = d.createElement('span');
    name.className = 'att-chip-name';
    name.textContent = rec.filename;
    name.title = rec.filename + ' (' + rec.size_label + ')';
    el.appendChild(name);
    var meta = d.createElement('span');
    meta.className = 'att-chip-meta';
    meta.textContent = rec.size_label;
    el.appendChild(meta);
    if (typeof onRemove === 'function') {
      var rm = d.createElement('button');
      rm.type = 'button';
      rm.className = 'att-chip-x';
      rm.textContent = '×';  // ×
      rm.setAttribute('aria-label', 'Remove attachment');
      rm.addEventListener('click', function () { onRemove(rec.id); });
      el.appendChild(rm);
    }
    return el;
  }

  // Walk a list of attachment dicts and render them into an existing
  // container. Returns the count rendered. Used both for the pending
  // chip row (with the remove button) and for the per-turn chip strip
  // (no remove button).
  function renderInto(container, list, opts) {
    if (!container) return 0;
    var doc = container.ownerDocument
            || (typeof document !== 'undefined' ? document : null);
    container.textContent = '';
    var arr = Array.isArray(list) ? list : [];
    var onRemove = opts && opts.onRemove;
    for (var i = 0; i < arr.length; i++) {
      container.appendChild(buildChip(doc, arr[i], onRemove));
    }
    return arr.length;
  }

  // Group an aggregate list (e.g. a turn's attachments) into the most
  // useful summary string for the federated search provider. Kept here
  // so the same logic feeds both the chip name layer AND any future
  // "find files I uploaded" surface.
  function summariseFilenames(list) {
    var arr = Array.isArray(list) ? list : [];
    var parts = [];
    for (var i = 0; i < arr.length; i++) {
      var rec = normalise(arr[i]);
      if (rec.filename) parts.push(rec.filename);
    }
    return parts.join(', ');
  }

  var _exports = {
    classify:          classify,
    iconFor:           iconFor,
    formatSize:        formatSize,
    isAcceptable:      isAcceptable,
    normalise:         normalise,
    buildChip:         buildChip,
    renderInto:        renderInto,
    summariseFilenames: summariseFilenames,
    MAX_INLINE_BYTES:  MAX_INLINE_BYTES,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.AttachmentChips = _exports;
  }
})();
