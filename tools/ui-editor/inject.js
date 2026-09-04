// Click-to-edit overlay, injected into every proxied page by server.js.
// Self-contained: no postMessage protocol, no parent-page dependency.
// ponytail: element identity is a DOM-index path (tag+childIndex per ancestor),
// stable only while page structure is static; a page that reorders/conditionally
// renders before load will drift. Upgrade to a content-hash id if that bites.
(function () {
  'use strict';
  var params = new URLSearchParams(document.currentScript.src.split('?')[1] || '');
  var KEY = params.get('key');
  var overrides = {};
  var selected = null; // {el, id}
  var editMode = false;
  var saveTimer = null;

  function pathId(el) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      var parent = node.parentElement;
      var idx = parent ? Array.prototype.indexOf.call(parent.children, node) : 0;
      parts.unshift(node.tagName + idx);
      node = parent;
    }
    return parts.join('>');
  }

  function idToEl(id) {
    var parts = id.split('>');
    var node = document.documentElement;
    for (var i = 0; i < parts.length; i++) {
      var tag = parts[i].replace(/\d+$/, '');
      var idx = parseInt(parts[i].slice(tag.length), 10);
      if (!node.children[idx] || node.children[idx].tagName !== tag) return null;
      node = node.children[idx];
    }
    return node;
  }

  function applyOverride(el, o) {
    if (o.style) for (var k in o.style) el.style[k] = o.style[k];
    if (typeof o.text === 'string') el.textContent = o.text;
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    setStatus('saving...');
    saveTimer = setTimeout(function () {
      fetch('/api/save', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ key: KEY, overrides: overrides })
      }).then(function () { setStatus('saved'); });
    }, 400);
  }

  function setOverride(el, patch) {
    var id = el.getAttribute('data-uieditor-id');
    var o = overrides[id] || (overrides[id] = {});
    if (patch.style) o.style = Object.assign(o.style || {}, patch.style);
    if (typeof patch.text === 'string') o.text = patch.text;
    scheduleSave();
  }

  // ---- floating toolbar (fixed, isolated inline styles) ----
  var bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483647;background:#1e1e24;color:#eee;' +
    'font:12px/1.4 -apple-system,Segoe UI,sans-serif;border-radius:8px;padding:8px 10px;box-shadow:0 4px 16px rgba(0,0,0,.4);' +
    'display:flex;flex-direction:column;gap:6px;min-width:190px;';
  bar.innerHTML =
    '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;">' +
    '<input type="checkbox" id="ue-toggle"> <b>Edit mode</b></label>' +
    '<div id="ue-panel" style="display:none;flex-direction:column;gap:6px;border-top:1px solid #444;padding-top:6px;">' +
    '<div style="display:flex;gap:6px;align-items:center;">BG <input type="color" id="ue-bg"></div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Text <input type="color" id="ue-fg"></div>' +
    '<div style="display:flex;gap:6px;align-items:center;">Font <input type="number" id="ue-fs" min="6" max="200" style="width:56px;"> px</div>' +
    '<button id="ue-edittext" style="cursor:pointer;">Edit text</button>' +
    '<button id="ue-reset" style="cursor:pointer;">Reset this page</button>' +
    '</div>' +
    '<div id="ue-status" style="opacity:.6;">idle</div>';
  function mount() { document.documentElement.appendChild(bar); }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  function setStatus(s) { var el = bar.querySelector('#ue-status'); if (el) el.textContent = s; }
  function within(node) { return bar.contains(node); }

  // ---- selection highlight + resize handles ----
  var highlight = document.createElement('div');
  highlight.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #4da3ff;z-index:2147483646;display:none;box-sizing:border-box;';
  var hoverBox = document.createElement('div');
  hoverBox.style.cssText = 'position:fixed;pointer-events:none;border:1px dashed #4da3ff;z-index:2147483645;display:none;box-sizing:border-box;';
  var handles = ['nw', 'ne', 'sw', 'se'].map(function (pos) {
    var h = document.createElement('div');
    h.dataset.pos = pos;
    h.style.cssText = 'position:fixed;width:10px;height:10px;background:#4da3ff;border-radius:50%;z-index:2147483647;display:none;cursor:' +
      (pos === 'nw' || pos === 'se' ? 'nwse-resize' : 'nesw-resize') + ';';
    return h;
  });
  function mountOverlays() {
    document.documentElement.appendChild(highlight);
    document.documentElement.appendChild(hoverBox);
    handles.forEach(function (h) { document.documentElement.appendChild(h); });
  }
  if (document.body) mountOverlays(); else document.addEventListener('DOMContentLoaded', mountOverlays);

  function positionHighlight() {
    if (!selected) { highlight.style.display = 'none'; handles.forEach(function (h) { h.style.display = 'none'; }); return; }
    var r = selected.getBoundingClientRect();
    highlight.style.display = 'block';
    highlight.style.left = r.left + 'px'; highlight.style.top = r.top + 'px';
    highlight.style.width = r.width + 'px'; highlight.style.height = r.height + 'px';
    var pts = { nw: [r.left, r.top], ne: [r.right, r.top], sw: [r.left, r.bottom], se: [r.right, r.bottom] };
    handles.forEach(function (h) {
      var p = pts[h.dataset.pos];
      h.style.display = 'block';
      h.style.left = (p[0] - 5) + 'px'; h.style.top = (p[1] - 5) + 'px';
    });
  }
  window.addEventListener('scroll', positionHighlight, true);
  window.addEventListener('resize', positionHighlight);

  function select(el) {
    selected = el;
    positionHighlight();
    var cs = getComputedStyle(el);
    bar.querySelector('#ue-bg').value = rgbToHex(cs.backgroundColor) || '#ffffff';
    bar.querySelector('#ue-fg').value = rgbToHex(cs.color) || '#000000';
    bar.querySelector('#ue-fs').value = parseInt(cs.fontSize, 10) || 14;
  }
  function rgbToHex(rgb) {
    var m = rgb.match(/\d+/g);
    if (!m) return null;
    return '#' + m.slice(0, 3).map(function (n) { return (+n).toString(16).padStart(2, '0'); }).join('');
  }

  document.addEventListener('mouseover', function (e) {
    if (!editMode || within(e.target)) return;
    var r = e.target.getBoundingClientRect();
    hoverBox.style.display = 'block';
    hoverBox.style.left = r.left + 'px'; hoverBox.style.top = r.top + 'px';
    hoverBox.style.width = r.width + 'px'; hoverBox.style.height = r.height + 'px';
  }, true);
  document.addEventListener('mouseout', function () { hoverBox.style.display = 'none'; }, true);

  document.addEventListener('click', function (e) {
    if (!editMode || within(e.target)) return;
    e.preventDefault(); e.stopPropagation();
    select(e.target);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { selected = null; positionHighlight(); }
  });

  // move: drag inside the highlighted box (not on a handle)
  highlight.style.pointerEvents = 'none'; // handled via mousedown on document while editMode+selected, hit-testing the box rect
  document.addEventListener('mousedown', function (e) {
    if (!editMode || !selected || within(e.target)) return;
    var r = selected.getBoundingClientRect();
    var overBox = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    if (!overBox || e.target !== selected && !selected.contains(e.target)) {
      if (!overBox) return;
    }
    e.preventDefault(); e.stopPropagation();
    var startX = e.clientX, startY = e.clientY;
    var cs = getComputedStyle(selected);
    var startLeft = parseFloat(cs.marginLeft) || 0, startTop = parseFloat(cs.marginTop) || 0;
    if (cs.position === 'static') selected.style.position = 'relative';
    function onMove(ev) {
      selected.style.marginLeft = (startLeft + ev.clientX - startX) + 'px';
      selected.style.marginTop = (startTop + ev.clientY - startY) + 'px';
      positionHighlight();
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      setOverride(selected, { style: { position: selected.style.position, marginLeft: selected.style.marginLeft, marginTop: selected.style.marginTop } });
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, true);

  handles.forEach(function (h) {
    h.addEventListener('mousedown', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (!selected) return;
      var pos = h.dataset.pos;
      var r = selected.getBoundingClientRect();
      var startX = e.clientX, startY = e.clientY;
      var startW = r.width, startH = r.height;
      function onMove(ev) {
        var dx = ev.clientX - startX, dy = ev.clientY - startY;
        var w = startW, hgt = startH;
        if (pos === 'ne' || pos === 'se') w = Math.max(4, startW + dx);
        if (pos === 'nw' || pos === 'sw') w = Math.max(4, startW - dx);
        if (pos === 'sw' || pos === 'se') hgt = Math.max(4, startH + dy);
        if (pos === 'nw' || pos === 'ne') hgt = Math.max(4, startH - dy);
        selected.style.width = w + 'px';
        selected.style.height = hgt + 'px';
        positionHighlight();
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        setOverride(selected, { style: { width: selected.style.width, height: selected.style.height } });
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });

  bar.querySelector('#ue-toggle').addEventListener('change', function (e) {
    editMode = e.target.checked;
    bar.querySelector('#ue-panel').style.display = editMode ? 'flex' : 'none';
    if (!editMode) { selected = null; positionHighlight(); hoverBox.style.display = 'none'; }
  });
  bar.querySelector('#ue-bg').addEventListener('input', function (e) {
    if (!selected) return;
    selected.style.backgroundColor = e.target.value;
    setOverride(selected, { style: { backgroundColor: e.target.value } });
  });
  bar.querySelector('#ue-fg').addEventListener('input', function (e) {
    if (!selected) return;
    selected.style.color = e.target.value;
    setOverride(selected, { style: { color: e.target.value } });
  });
  bar.querySelector('#ue-fs').addEventListener('input', function (e) {
    if (!selected) return;
    var v = e.target.value + 'px';
    selected.style.fontSize = v;
    setOverride(selected, { style: { fontSize: v } });
  });
  bar.querySelector('#ue-edittext').addEventListener('click', function () {
    if (!selected) return;
    selected.contentEditable = 'true';
    selected.focus();
    function commit() {
      selected.contentEditable = 'false';
      setOverride(selected, { text: selected.textContent });
      selected.removeEventListener('blur', commit);
    }
    selected.addEventListener('blur', commit);
  });
  bar.querySelector('#ue-reset').addEventListener('click', function () {
    fetch('/api/reset', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ key: KEY })
    }).then(function () { location.reload(); });
  });

  // ---- init: tag every element with a stable id, then apply saved overrides ----
  function init() {
    var all = document.documentElement.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      if (within(all[i])) continue;
      all[i].setAttribute('data-uieditor-id', pathId(all[i]));
    }
    fetch('/api/load?key=' + encodeURIComponent(KEY)).then(function (r) { return r.json(); }).then(function (data) {
      overrides = data || {};
      Object.keys(overrides).forEach(function (id) {
        var el = idToEl(id);
        if (el) applyOverride(el, overrides[id]);
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
