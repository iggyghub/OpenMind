'use strict';

// mark-kit -- point at a live UI and leave notes for whoever fixes it.
//
// Ctrl+Shift+M toggles mark mode. While on, hovering outlines an element;
// clicking asks "what should change?" and records a mark (a stable locator,
// the text, the styles that usually matter, viewport + scroll, console errors
// since the last mark, whatever the host adds via opts.context, and the note).
// "Copy all" puts every mark on the clipboard as one paste. Marks survive
// reloads via localStorage. The bar can be dragged; its position is kept.
//
// Host-agnostic: no imports, no knowledge of the app it is loaded into
// (tests/boundary.test.js enforces this). A host calls
//   MarkKit.init(document, { context: function (el) { return {...}; } })
// and the context object is stored on each mark as `app`.
//
// Dual-mode export: window.MarkKit in a page, module.exports for Node tests.

(function () {
  var KEY = 'ui-marks';
  var POS_KEY = 'mark-kit-pos';
  var STYLE_PROPS = ['font-size', 'font-weight', 'line-height', 'color', 'background-color',
    'padding', 'margin', 'gap', 'border-radius', 'width', 'height'];

  function esc(id) { return typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id; }
  function ownClasses(el) {
    // uim- is ours; is-* are state classes that change under the mark.
    return el.classList ? Array.prototype.slice.call(el.classList)
      .filter(function (c) { return c.indexOf('uim-') !== 0 && c.indexOf('is-') !== 0; }) : [];
  }
  function shortText(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80); }

  // Full CSS path, stopping at the first ancestor with an id. Fallback locator.
  function cssPath(el) {
    var parts = [];
    while (el && el.tagName && el.tagName.toLowerCase() !== 'html') {
      if (el.id) { parts.unshift('#' + esc(el.id)); break; }
      var part = el.tagName.toLowerCase();
      var cls = ownClasses(el);
      if (cls.length) part += '.' + cls.map(esc).join('.');
      var parent = el.parentElement;
      if (parent) {
        var same = Array.prototype.filter.call(parent.children, function (s) { return s.tagName === el.tagName; });
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
      }
      parts.unshift(part);
      el = parent;
    }
    return parts.join(' > ');
  }

  // Short stable locator: nearest id'd ancestor + tag.classes, narrowed by text,
  // and an index among the matches only when text still leaves it ambiguous.
  function locate(el) {
    if (el.id) return { sel: '#' + esc(el.id) };
    var root = el.parentElement;
    while (root && !root.id) root = root.parentElement;
    var cls = ownClasses(el);
    var loc = {
      anchor: root ? '#' + esc(root.id) : '',
      sel: el.tagName.toLowerCase() + (cls.length ? '.' + cls.map(esc).join('.') : ''),
      text: shortText(el).slice(0, 40),
    };
    var role = el.getAttribute && el.getAttribute('role');
    if (role) loc.role = role;
    var found = candidates(root || el.ownerDocument, loc);
    if (found.length > 1) loc.nth = found.indexOf(el);
    return loc;
  }

  function candidates(root, loc) {
    if (!root || !root.querySelectorAll) return [];
    var c;
    try { c = Array.prototype.slice.call(root.querySelectorAll(loc.sel)); } catch (e) { return []; }
    if (c.length > 1 && loc.text) {
      var t = c.filter(function (n) { return shortText(n).slice(0, 40) === loc.text; });
      if (t.length) c = t;
    }
    return c;
  }

  // Find a mark's element again (after a re-render or a reload). Null if gone.
  function resolve(doc, mark) {
    var loc = mark.locator;
    if (loc) {
      var root = loc.anchor ? doc.querySelector(loc.anchor) : doc;
      var c = candidates(root, loc);
      if (c.length === 1) return c[0];
      if (c.length > 1 && loc.nth != null && c[loc.nth]) return c[loc.nth];
      if (c.length) return null;  // ambiguous: better no highlight than the wrong one
    }
    try { return mark.selector ? doc.querySelector(mark.selector) : null; } catch (e) { return null; }
  }

  function describe(el, computed, note, extra) {
    var styles = {};
    STYLE_PROPS.forEach(function (p) {
      var v = computed.getPropertyValue(p);
      if (v && v !== 'normal' && v !== 'none' && v !== '0px' && v !== 'rgba(0, 0, 0, 0)') styles[p] = v;
    });
    var mark = {
      note: note,
      locator: locate(el),
      selector: cssPath(el),
      text: shortText(el),
      styles: styles,
    };
    if (extra) Object.keys(extra).forEach(function (k) { mark[k] = extra[k]; });
    return mark;
  }

  function locText(loc) {
    if (!loc) return '';
    return [loc.anchor, loc.sel].filter(Boolean).join(' ') +
      (loc.text ? ' "' + loc.text + '"' : '') + (loc.nth != null ? ' #' + (loc.nth + 1) : '');
  }

  function format(marks) {
    return 'UI marks (' + marks.length + '):\n\n' + marks.map(function (m, i) {
      var app = m.app || (m.pane ? { route: m.pane } : {});
      var where = Object.keys(app).filter(function (k) { return app[k]; })
        .map(function (k) { return app[k]; }).join(' / ');
      var s = Object.keys(m.styles || {}).map(function (k) { return k + ': ' + m.styles[k]; }).join('; ');
      return (i + 1) + '. [' + (where || '?') + '] ' + m.note + '\n' +
        (m.locator ? '   locator: ' + locText(m.locator) + '\n' : '') +
        '   selector: ' + m.selector + '\n' +
        (m.text ? '   text: "' + m.text + '"\n' : '') +
        (m.viewport ? '   viewport: ' + m.viewport.w + 'x' + m.viewport.h +
          (m.scroll && (m.scroll.x || m.scroll.y) ? ', scroll ' + m.scroll.x + ',' + m.scroll.y : '') + '\n' : '') +
        (m.errors && m.errors.length ? '   errors: ' + m.errors.join(' | ') + '\n' : '') +
        '   styles: ' + s;
    }).join('\n\n');
  }

  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } }
  function save(marks) { try { localStorage.setItem(KEY, JSON.stringify(marks)); } catch (e) {} }

  // Console errors since the last mark. Installed at init, so it also catches
  // errors that happen while mark mode is off.
  function watchErrors(win) {
    var buf = [];
    function push(msg) { buf.push(String(msg).slice(0, 200)); if (buf.length > 20) buf.shift(); }
    win.addEventListener('error', function (e) { push(e.message || e); });
    win.addEventListener('unhandledrejection', function (e) { push('unhandled: ' + (e.reason && e.reason.message || e.reason)); });
    var orig = win.console && win.console.error;
    if (orig) win.console.error = function () {
      push(Array.prototype.map.call(arguments, String).join(' '));
      return orig.apply(this, arguments);
    };
    return function take() { var out = buf; buf = []; return out; };
  }

  // Browser-only wiring below.
  function init(doc, opts) {
    opts = opts || {};
    var win = doc.defaultView;
    var takeErrors = watchErrors(win);
    var on = false, hovered = null, marks = load();
    var css = doc.createElement('style');
    css.textContent =
      '.uim-hover{outline:2px dashed var(--accent,#7c5cfc)!important;outline-offset:1px!important;cursor:crosshair!important}' +
      '.uim-marked{outline:2px solid var(--color-warning,#e5a50a)!important;outline-offset:1px!important}' +
      '#uim-bar{position:fixed;right:12px;bottom:12px;z-index:2147483647;display:flex;gap:6px;align-items:center;' +
      'padding:6px 10px;border-radius:10px;background:#1b1b22;border:1px solid var(--accent,#7c5cfc);' +
      'box-shadow:0 4px 16px rgba(0,0,0,.4);font:12px system-ui;color:#ddd}' +
      '#uim-bar[hidden]{display:none}' +
      '#uim-grip{cursor:move;user-select:none}' +
      '#uim-bar button{font:inherit;padding:2px 8px;border-radius:6px;border:1px solid #444;background:#2a2a33;color:#ddd;cursor:pointer}';
    doc.head.appendChild(css);
    var bar = doc.createElement('div');
    bar.id = 'uim-bar';
    bar.hidden = true;
    // Electron has no window.prompt(), so the note is typed into the bar itself.
    bar.innerHTML = '<span id="uim-grip" title="Drag to move">&#x2630; Mark mode: <b id="uim-n">0</b></span>' +
      '<input id="uim-note" placeholder="Click something, then say what should change" hidden ' +
      'style="width:280px;font:inherit;padding:2px 6px;border-radius:6px;border:1px solid #555;background:#111;color:#eee">' +
      '<button id="uim-copy">Copy all</button><button id="uim-clear">Clear</button><button id="uim-off">Done</button>';
    doc.body.appendChild(bar);
    var count = bar.querySelector('#uim-n');
    var input = bar.querySelector('#uim-note');
    var pending = null;
    function refresh() { count.textContent = marks.length; }
    refresh();

    // Drag by the grip; position kept and clamped back on screen.
    function place(pos) {
      if (!pos) return;
      var x = Math.max(0, Math.min(pos.x, win.innerWidth - bar.offsetWidth));
      var y = Math.max(0, Math.min(pos.y, win.innerHeight - bar.offsetHeight));
      bar.style.left = x + 'px'; bar.style.top = y + 'px';
      bar.style.right = 'auto'; bar.style.bottom = 'auto';
    }
    function savedPos() { try { return JSON.parse(localStorage.getItem(POS_KEY)); } catch (e) { return null; } }
    bar.querySelector('#uim-grip').addEventListener('mousedown', function (e) {
      e.preventDefault();
      var r = bar.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      function move(ev) { place({ x: ev.clientX - dx, y: ev.clientY - dy }); }
      function up() {
        doc.removeEventListener('mousemove', move, true); doc.removeEventListener('mouseup', up, true);
        var r2 = bar.getBoundingClientRect();
        try { localStorage.setItem(POS_KEY, JSON.stringify({ x: r2.left, y: r2.top })); } catch (er) {}
      }
      doc.addEventListener('mousemove', move, true); doc.addEventListener('mouseup', up, true);
    });

    // Outline every mark that still resolves. Re-run after re-renders while on.
    function highlight() {
      Array.prototype.forEach.call(doc.querySelectorAll('.uim-marked'), function (m) {
        if (m !== pending) m.classList.remove('uim-marked');
      });
      if (!on) return;
      marks.forEach(function (m) { var el = resolve(doc, m); if (el) el.classList.add('uim-marked'); });
    }
    var timer = null;
    // childList only: our own class toggles are attribute changes, so no loop.
    var observer = typeof MutationObserver !== 'undefined' ? new MutationObserver(function () {
      clearTimeout(timer); timer = setTimeout(highlight, 150);
    }) : null;

    function setPending(el) {
      if (pending) pending.classList.remove('uim-marked');
      pending = el;
      input.hidden = !el; input.value = '';
      if (el) { el.classList.add('uim-marked'); input.focus(); }
    }
    // Saves a typed-but-unsubmitted note too, so Done / Copy all / clicking the
    // next element never silently drops it (the first live mark was lost that way).
    function commit() {
      var note = input.value.trim();
      if (!pending || !note) return;
      var extra = {
        created: new Date().toISOString(),
        url: win.location ? win.location.href.split('?')[0].split('#')[0] + win.location.hash : '',  // no query: may hold tokens
        viewport: { w: win.innerWidth, h: win.innerHeight },
        scroll: { x: Math.round(win.scrollX || 0), y: Math.round(win.scrollY || 0) },
        errors: takeErrors(),
      };
      if (opts.context) { try { extra.app = opts.context(pending) || {}; } catch (e) { extra.app = {}; } }
      marks.push(describe(pending, win.getComputedStyle(pending), note, extra));
      save(marks); refresh();
      pending = null; input.hidden = true;  // keep the solid outline on the marked element
    }
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Escape') setPending(null);
      else if (e.key === 'Enter') commit();
    });

    function setOn(v) {
      if (!v) commit();
      on = v; bar.hidden = !v;
      if (!v) setPending(null);
      if (!v && hovered) { hovered.classList.remove('uim-hover'); hovered = null; }
      if (v) place(savedPos());
      if (observer) { if (v) observer.observe(doc.body, { childList: true, subtree: true }); else observer.disconnect(); }
      highlight();
    }
    function inBar(t) { return t === bar || bar.contains(t); }

    doc.addEventListener('keydown', function (e) {
      if (e.ctrlKey && e.shiftKey && !e.altKey && (e.key === 'M' || e.key === 'm')) { e.preventDefault(); setOn(!on); }
      else if (on && e.key === 'Escape') setOn(false);
    }, true);
    doc.addEventListener('mouseover', function (e) {
      if (!on || inBar(e.target)) return;
      if (hovered) hovered.classList.remove('uim-hover');
      hovered = e.target; hovered.classList.add('uim-hover');
    }, true);
    // Capture phase + stopPropagation so marking a button doesn't also press it.
    ['mousedown', 'mouseup', 'click'].forEach(function (type) {
      doc.addEventListener(type, function (e) {
        if (!on || inBar(e.target)) return;
        e.preventDefault(); e.stopPropagation();
        if (type !== 'click') return;
        e.target.classList.remove('uim-hover');
        commit();
        setPending(e.target);
      }, true);
    });
    bar.querySelector('#uim-copy').addEventListener('click', function () {
      commit();
      var text = format(marks);
      var btn = bar.querySelector('#uim-copy');
      function fallback() {  // execCommand path if the async clipboard API is refused
        var ta = doc.createElement('textarea');
        ta.value = text; doc.body.appendChild(ta); ta.select();
        var ok = doc.execCommand('copy'); ta.remove();
        btn.textContent = ok ? 'Copied ' + marks.length : 'Copy failed';
      }
      if (!navigator.clipboard) return fallback();
      navigator.clipboard.writeText(text).then(function () { btn.textContent = 'Copied ' + marks.length; }, fallback);
    });
    bar.querySelector('#uim-clear').addEventListener('click', function () {
      marks = []; save(marks); refresh();
      setPending(null); highlight();
      bar.querySelector('#uim-copy').textContent = 'Copy all';
    });
    bar.querySelector('#uim-off').addEventListener('click', function () { setOn(false); });
  }

  var _exports = { init: init, cssPath: cssPath, locate: locate, resolve: resolve, describe: describe, format: format };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.MarkKit = _exports;
  }
})();
