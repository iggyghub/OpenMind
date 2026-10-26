'use strict';

// mark-kit -- point at a live UI and leave notes for whoever fixes it.
//
// Ctrl+Shift+M toggles mark mode. While on, hovering outlines an element;
// clicking selects it and the bar asks what kind of change (Fix / Remove /
// Move / Add / Link / Bug / Idea), for this element or all like it, and a
// note. Each mark records a stable locator, the text, the styles that usually
// matter, viewport + scroll, console errors since the last mark, and whatever
// the host adds via opts.context. "Copy all" puts every mark on the clipboard
// as one paste. Marks survive reloads via localStorage. The bar can be dragged;
// its position is kept.
//
// Host-agnostic: no imports, no knowledge of the app it is loaded into
// (tests/boundary.test.js enforces this). A host calls
//   MarkKit.init(document, { context: function (el) { return {...}; } })
// and the context object is stored on each mark as `app`. Optional hooks:
//   capture(rect) -> Promise<png data URL | null>   screenshot of a viewport rect
//   store: { add(mark) -> Promise<stored mark | null>,
//            list() -> Promise<marks | null>,
//            update(id, patch) -> Promise<mark | null> }
// With a store, marks are written there (localStorage only buffers the ones not
// stored yet) and the bar shows what the store holds. init() returns
// { reload } so a host can refresh the bar when its store changes.
// Inputs are blacked out while the host captures, and input values are never
// recorded (field text is left out of every mark).
//
// Dual-mode export: window.MarkKit in a page, module.exports for Node tests.

(function () {
  var KEY = 'ui-marks';
  var POS_KEY = 'mark-kit-pos';
  var STYLE_PROPS = ['font-size', 'font-weight', 'line-height', 'color', 'background-color',
    'padding', 'margin', 'gap', 'border-radius', 'width', 'height'];
  var TYPES = ['fix', 'remove', 'move', 'add', 'link', 'bug', 'idea'];
  var ADD_KINDS = ['page', 'tab', 'section', 'control', 'field'];
  var RELATIONS = ['opens', 'shows data from', 'stays in sync with', 'looks like'];

  function esc(id) { return typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(id) : id; }
  function ownClasses(el) {
    // uim- is ours; is-* are state classes that change under the mark.
    return el.classList ? Array.prototype.slice.call(el.classList)
      .filter(function (c) { return c.indexOf('uim-') !== 0 && c.indexOf('is-') !== 0; }) : [];
  }
  // Text the user typed never goes into a mark: fields give '', and a container
  // drops its fields' text. Static UI text is what locates an element.
  var FIELDS = 'input,textarea,select,[contenteditable]:not([contenteditable="false"])';
  function isField(el) {
    return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || !!el.isContentEditable;
  }
  function shortText(el) {
    if (isField(el)) return '';
    var src = el;
    if (el.querySelector && el.cloneNode && el.querySelector(FIELDS)) {
      src = el.cloneNode(true);
      Array.prototype.forEach.call(src.querySelectorAll(FIELDS), function (f) { f.remove(); });
    }
    return (src.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  }
  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

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

  // "Like this" = same tag and same (non-state) class list.
  function likeSelector(el) {
    var cls = ownClasses(el);
    return el.tagName.toLowerCase() + (cls.length ? '.' + cls.map(esc).join('.') : '');
  }

  // Short stable locator: nearest id'd ancestor + tag.classes, narrowed by text,
  // and an index among the matches only when text still leaves it ambiguous.
  function locate(el) {
    if (el.id) return { sel: '#' + esc(el.id) };
    var root = el.parentElement;
    while (root && !root.id) root = root.parentElement;
    var loc = {
      anchor: root ? '#' + esc(root.id) : '',
      sel: likeSelector(el),
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

  // Just enough to find the other end of a Link mark again.
  function pointer(el) { return { locator: locate(el), selector: cssPath(el), text: shortText(el) }; }

  function locText(loc) {
    if (!loc) return '';
    return [loc.anchor, loc.sel].filter(Boolean).join(' ') +
      (loc.text ? ' "' + loc.text + '"' : '') + (loc.nth != null ? ' #' + (loc.nth + 1) : '');
  }

  // "(Add section, all like this: 7 matched)" -- empty for a plain one-off Fix.
  function intent(m) {
    var bits = [];
    if (m.type && (m.type !== 'fix' || m.scope === 'all')) bits.push(cap(m.type) + (m.kind ? ' ' + m.kind : ''));
    if (m.scope === 'all') bits.push('all like this: ' + m.matched + ' matched (' + m.match + ')');
    return bits.length ? '(' + bits.join(', ') + ') ' : '';
  }

  function format(marks) {
    return 'UI marks (' + marks.length + '):\n\n' + marks.map(function (m, i) {
      var app = m.app || (m.pane ? { route: m.pane } : {});
      var where = Object.keys(app).filter(function (k) { return app[k]; })
        .map(function (k) { return app[k]; }).join(' / ');
      var s = Object.keys(m.styles || {}).map(function (k) { return k + ': ' + m.styles[k]; }).join('; ');
      return (i + 1) + '. [' + (where || '?') + '] ' + intent(m) + m.note + '\n' +
        (m.locator ? '   locator: ' + locText(m.locator) + '\n' : '') +
        '   selector: ' + m.selector + '\n' +
        (m.text ? '   text: "' + m.text + '"\n' : '') +
        (m.link ? '   ' + m.link.relation + ': ' + (locText(m.link.locator) || m.link.selector) + '\n' : '') +
        (m.viewport ? '   viewport: ' + m.viewport.w + 'x' + m.viewport.h +
          (m.scroll && (m.scroll.x || m.scroll.y) ? ', scroll ' + m.scroll.x + ',' + m.scroll.y : '') + '\n' : '') +
        (m.errors && m.errors.length ? '   errors: ' + m.errors.join(' | ') + '\n' : '') +
        (m.shot_path ? '   screenshot: ' + m.shot_path + '\n' : '') +
        '   styles: ' + s;
    }).join('\n\n');
  }

  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } }
  // Screenshots stay in memory / the host store, never in localStorage (quota).
  // With a store only the marks it hasn't confirmed yet are buffered here.
  function save(marks) {
    try {
      localStorage.setItem(KEY, JSON.stringify(marks.filter(function (m) { return !m.stored; }).map(function (m) {
        var c = Object.assign({}, m); delete c.shot; return c;
      })));
    } catch (e) {}
  }

  // Still waiting on a fix (or never given a status: no store).
  function actionable(m) { return !m.status || m.status === 'open' || m.status === 'reopened'; }
  function newId() { return 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  // Viewport rect around an element's box with some margin, clamped to the window.
  function shotRect(r, vw, vh, margin) {
    var x = Math.max(0, Math.floor(r.left - margin)), y = Math.max(0, Math.floor(r.top - margin));
    var w = Math.min(vw, Math.ceil(r.right + margin)) - x, h = Math.min(vh, Math.ceil(r.bottom + margin)) - y;
    return w > 0 && h > 0 ? { x: x, y: y, width: w, height: h } : null;
  }

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

  function chips(name, values, labels) {
    return '<span class="uim-chips" data-group="' + name + '">' + values.map(function (v, i) {
      return '<button type="button" data-v="' + v + '">' + (labels ? labels[i] : cap(v)) + '</button>';
    }).join('') + '</span>';
  }
  function options(values) {
    return values.map(function (v) { return '<option value="' + v + '">' + v + '</option>'; }).join('');
  }

  // Browser-only wiring below.
  function init(doc, opts) {
    opts = opts || {};
    var win = doc.defaultView;
    var takeErrors = watchErrors(win);
    var on = false, hovered = null, store = opts.store;
    var marks = load().map(function (m) { if (!m.id) m.id = newId(); return m; });  // pre-store marks had none
    var css = doc.createElement('style');
    css.textContent =
      '.uim-hover{outline:2px dashed var(--accent,#7c5cfc)!important;outline-offset:1px!important;cursor:crosshair!important}' +
      '.uim-marked{outline:2px solid var(--color-warning,#e5a50a)!important;outline-offset:1px!important}' +
      '.uim-like{outline:1px dotted var(--color-warning,#e5a50a)!important;outline-offset:1px!important}' +
      '.uim-link{outline:2px solid #3fa7ff!important;outline-offset:1px!important}' +
      '#uim-bar{position:fixed;right:12px;bottom:12px;z-index:2147483647;display:flex;flex-direction:column;gap:6px;' +
      'padding:6px 10px;border-radius:10px;background:#1b1b22;border:1px solid var(--accent,#7c5cfc);max-width:calc(100vw - 24px);' +
      'box-shadow:0 4px 16px rgba(0,0,0,.4);font:12px system-ui;color:#ddd}' +
      '#uim-bar[hidden],#uim-bar [hidden]{display:none!important}' +
      '#uim-bar .uim-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}' +
      '#uim-grip{cursor:move;user-select:none}' +
      '#uim-bar button,#uim-bar select{font:inherit;padding:2px 8px;border-radius:6px;border:1px solid #444;background:#2a2a33;color:#ddd;cursor:pointer}' +
      '#uim-bar .uim-chips{display:inline-flex;gap:2px}' +
      '#uim-bar .uim-chips button.on{background:var(--accent,#7c5cfc);border-color:var(--accent,#7c5cfc);color:#fff}' +
      '.uim-mask #uim-bar{visibility:hidden!important}' +
      '.uim-mask .uim-hover,.uim-mask .uim-like,.uim-mask .uim-link{outline:none!important}' +
      '.uim-mask input,.uim-mask textarea,.uim-mask select,.uim-mask [contenteditable]:not([contenteditable="false"])' +
      '{color:transparent!important;-webkit-text-fill-color:transparent!important;background:#000!important;caret-color:transparent!important}' +
      '#uim-thumb{height:36px;border:1px solid #444;border-radius:4px;cursor:zoom-in}' +
      '#uim-sketch{position:fixed;inset:5vh 5vw;z-index:2147483647;background:#000c;display:flex;flex-direction:column;gap:6px;' +
      'align-items:center;justify-content:center;padding:10px;border-radius:10px}' +
      '#uim-sketch canvas{max-width:100%;max-height:calc(100% - 40px);cursor:crosshair;background:#fff}' +
      '#uim-note{flex:1;min-width:240px;font:inherit;padding:2px 6px;border-radius:6px;border:1px solid #555;background:#111;color:#eee}';
    doc.head.appendChild(css);
    var bar = doc.createElement('div');
    bar.id = 'uim-bar';
    bar.hidden = true;
    // Electron has no window.prompt(), so everything is picked/typed in the bar itself.
    bar.innerHTML =
      '<div class="uim-row"><span id="uim-grip" title="Drag to move">&#x2630; Mark mode: <b id="uim-n">0</b></span>' +
      '<button id="uim-copy">Copy all</button><button id="uim-clear">Clear</button><button id="uim-off">Done</button></div>' +
      '<div class="uim-row" id="uim-edit" hidden>' + chips('type', TYPES) +
      chips('scope', ['one', 'all'], ['This one', 'All like this <b id="uim-count"></b>']) +
      '<select id="uim-kind" hidden title="What to add">' + options(ADD_KINDS) + '</select>' +
      '<select id="uim-rel" hidden title="How the two are linked">' + options(RELATIONS) + '</select>' +
      '<span id="uim-link-hint" hidden>then click the other element</span>' +
      '<img id="uim-thumb" hidden alt="screenshot" title="Screenshot saved with this mark">' +
      '<button id="uim-sketch-btn" hidden title="Draw boxes on the screenshot to show where and roughly how">Sketch</button></div>' +
      '<div id="uim-sketch" hidden><canvas></canvas><div class="uim-row">' +
      '<span>Drag to draw boxes</span><button id="uim-sk-undo">Undo</button><button id="uim-sk-done">Done</button></div></div>' +
      '<div class="uim-row" id="uim-note-row" hidden><input id="uim-note" placeholder="What should change? Enter saves, Esc cancels"></div>';
    doc.body.appendChild(bar);
    function $(id) { return bar.querySelector('#' + id); }
    var count = $('uim-n'), input = $('uim-note'), editRow = $('uim-edit'), noteRow = $('uim-note-row');
    var kindSel = $('uim-kind'), relSel = $('uim-rel'), linkHint = $('uim-link-hint');
    var pending = null, linkTo = null, likes = [], shot = null, sketch = [], shotSeq = 0;
    var thumb = $('uim-thumb'), sketchBtn = $('uim-sketch-btn'), sketchBox = $('uim-sketch');
    var choice = { type: 'fix', scope: 'one' };
    function refresh() { count.textContent = marks.filter(actionable).length; }
    refresh();
    if (store) $('uim-clear').hidden = true;  // the store keeps every mark; nothing is deleted

    // Push buffered marks to the store; merge what it confirms back in by id.
    function push(m) {
      Promise.resolve(store.add(Object.assign({}, m))).then(function (saved) {
        if (!saved || !saved.id) return;  // offline / refused: stays buffered for next time
        var i = marks.findIndex(function (x) { return x.id === saved.id; });
        if (i < 0) return;
        marks[i] = Object.assign({}, saved, { stored: true });
        save(marks); refresh();
      }, function () {});
    }
    function reload() {
      if (!store) return Promise.resolve();
      marks.forEach(function (m) { if (!m.stored) push(m); });
      return Promise.resolve(store.list()).then(function (list) {
        if (!Array.isArray(list)) return;
        var ids = {};
        list.forEach(function (m) { m.stored = true; ids[m.id] = true; });
        marks = list.concat(marks.filter(function (m) { return !m.stored && !ids[m.id]; }));
        save(marks); refresh(); highlight();
      }, function () {});
    }

    // Drag by the grip; position kept and clamped back on screen.
    function place(pos) {
      if (!pos) return;
      var x = Math.max(0, Math.min(pos.x, win.innerWidth - bar.offsetWidth));
      var y = Math.max(0, Math.min(pos.y, win.innerHeight - bar.offsetHeight));
      bar.style.left = x + 'px'; bar.style.top = y + 'px';
      bar.style.right = 'auto'; bar.style.bottom = 'auto';
    }
    function savedPos() { try { return JSON.parse(localStorage.getItem(POS_KEY)); } catch (e) { return null; } }
    $('uim-grip').addEventListener('mousedown', function (e) {
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

    function clearClass(cls, keep) {
      Array.prototype.forEach.call(doc.querySelectorAll('.' + cls), function (m) {
        if (m !== keep) m.classList.remove(cls);
      });
    }
    // Outline every mark that still resolves. Re-run after re-renders while on.
    function highlight() {
      clearClass('uim-marked', pending);
      if (!on) return;
      marks.filter(actionable).forEach(function (m) { var el = resolve(doc, m); if (el) el.classList.add('uim-marked'); });
    }
    var timer = null;
    // childList only: our own class toggles are attribute changes, so no loop.
    var observer = typeof MutationObserver !== 'undefined' ? new MutationObserver(function () {
      clearTimeout(timer); timer = setTimeout(highlight, 150);
    }) : null;

    // Reflect `choice` in the chips + extra pickers, and the "like this" set.
    function showChoice() {
      Array.prototype.forEach.call(bar.querySelectorAll('.uim-chips button'), function (b) {
        b.classList.toggle('on', choice[b.parentNode.getAttribute('data-group')] === b.getAttribute('data-v'));
      });
      kindSel.hidden = choice.type !== 'add';
      sketchBtn.hidden = choice.type !== 'add' || !shot;
      relSel.hidden = linkHint.hidden = choice.type !== 'link';
      linkHint.textContent = linkTo ? 'linked to "' + shortText(linkTo).slice(0, 30) + '"' : 'then click the other element';
      likes.forEach(function (el) { el.classList.remove('uim-like'); });
      likes = [];
      if (pending && choice.scope === 'all') {
        try { likes = Array.prototype.slice.call(doc.querySelectorAll(likeSelector(pending))); } catch (e) { likes = [pending]; }
        likes.forEach(function (el) { if (el !== pending) el.classList.add('uim-like'); });
      }
      $('uim-count').textContent = pending && choice.scope === 'all' ? '(' + likes.length + ')' : '';
    }
    editRow.addEventListener('click', function (e) {
      var b = e.target.closest('.uim-chips button');
      if (!b) return;
      choice[b.parentNode.getAttribute('data-group')] = b.getAttribute('data-v');
      if (choice.type !== 'link' && linkTo) { linkTo.classList.remove('uim-link'); linkTo = null; }
      showChoice(); input.focus();
    });

    function setPending(el) {
      if (pending) pending.classList.remove('uim-marked');
      if (linkTo) linkTo.classList.remove('uim-link');
      pending = el; linkTo = null; shot = null; sketch = []; shotSeq++;
      choice = { type: 'fix', scope: 'one' };
      editRow.hidden = noteRow.hidden = !el;
      thumb.hidden = true; sketchBox.hidden = true;
      input.value = '';
      if (el) { el.classList.add('uim-marked'); input.focus(); takeShot(el); }
      showChoice();
    }

    // Ask the host for a screenshot of the element (plus margin) as it looks now,
    // with inputs blacked out and our own bar hidden. No hook = no image.
    function takeShot(el) {
      if (!opts.capture) return;
      var seq = shotSeq;
      var rect = shotRect(el.getBoundingClientRect(), win.innerWidth, win.innerHeight, 40);
      if (!rect) return;
      var root = doc.documentElement, done = false;
      function unmask() { if (!done) { done = true; root.classList.remove('uim-mask'); } }
      root.classList.add('uim-mask');
      setTimeout(unmask, 3000);  // a host that never answers must not leave the page masked
      // A few frames so the mask is painted before the host grabs pixels.
      // ponytail: timer, not requestAnimationFrame -- rAF never fires in a background tab.
      setTimeout(function () {
        Promise.resolve(opts.capture(rect)).then(function (url) {
          unmask();
          if (seq !== shotSeq || !url) return;  // a newer pick superseded this one
          shot = url; thumb.src = url; thumb.hidden = false; showChoice();
        }, unmask);
      }, 80);
    }

    // Add marks: draw boxes on the screenshot. The boxes are burned into the image.
    var canvas = sketchBox.querySelector('canvas'), ctx2d = canvas.getContext && canvas.getContext('2d'), base = null;
    function redraw() {
      ctx2d.drawImage(base, 0, 0);
      ctx2d.strokeStyle = '#ff3b6b'; ctx2d.lineWidth = 3;
      sketch.forEach(function (b) { ctx2d.strokeRect(b.x, b.y, b.w, b.h); });
    }
    function openSketch() {
      if (!shot || !ctx2d) return;
      base = new Image();
      base.onload = function () {
        canvas.width = base.naturalWidth; canvas.height = base.naturalHeight;
        sketch = []; redraw(); sketchBox.hidden = false;
      };
      base.src = shot;
    }
    function canvasPt(e) {
      var r = canvas.getBoundingClientRect();
      return { x: (e.clientX - r.left) * canvas.width / r.width, y: (e.clientY - r.top) * canvas.height / r.height };
    }
    canvas.addEventListener('mousedown', function (e) {
      e.preventDefault();
      var a = canvasPt(e), box = { x: a.x, y: a.y, w: 0, h: 0 };
      sketch.push(box);
      function move(ev) {
        var b = canvasPt(ev);
        box.x = Math.min(a.x, b.x); box.y = Math.min(a.y, b.y);
        box.w = Math.abs(b.x - a.x); box.h = Math.abs(b.y - a.y); redraw();
      }
      function up() {
        doc.removeEventListener('mousemove', move, true); doc.removeEventListener('mouseup', up, true);
        if (box.w < 4 || box.h < 4) { sketch.pop(); redraw(); }
      }
      doc.addEventListener('mousemove', move, true); doc.addEventListener('mouseup', up, true);
    });
    sketchBtn.addEventListener('click', openSketch);
    thumb.addEventListener('click', function () { if (choice.type === 'add') openSketch(); });
    $('uim-sk-undo').addEventListener('click', function () { sketch.pop(); redraw(); });
    $('uim-sk-done').addEventListener('click', function () {
      if (sketch.length) { shot = canvas.toDataURL('image/png'); thumb.src = shot; }
      sketchBox.hidden = true; input.focus();
    });
    // Saves a typed-but-unsubmitted mark too, so Done / Copy all / clicking the
    // next element never silently drops it (the first live mark was lost that way).
    // A plain Fix needs a note; any other chosen type says enough on its own.
    function commit() {
      var note = input.value.trim();
      if (!pending || (!note && choice.type === 'fix')) return;
      var extra = {
        id: newId(),
        type: choice.type,
        created: new Date().toISOString(),
        url: win.location ? win.location.href.split('?')[0].split('#')[0] + win.location.hash : '',  // no query: may hold tokens
        viewport: { w: win.innerWidth, h: win.innerHeight },
        scroll: { x: Math.round(win.scrollX || 0), y: Math.round(win.scrollY || 0) },
        errors: takeErrors(),
      };
      if (choice.scope === 'all') { extra.scope = 'all'; extra.match = likeSelector(pending); extra.matched = likes.length; }
      if (choice.type === 'add') extra.kind = kindSel.value;
      if (shot) extra.shot = shot;
      if (sketch.length) extra.sketch = sketch.map(function (b) {
        return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.w), h: Math.round(b.h) };
      });
      if (choice.type === 'link' && linkTo) extra.link = Object.assign({ relation: relSel.value }, pointer(linkTo));
      if (opts.context) { try { extra.app = opts.context(pending) || {}; } catch (e) { extra.app = {}; } }
      var mark = describe(pending, win.getComputedStyle(pending), note, extra);
      marks.push(mark);
      save(marks); refresh();
      if (store) push(mark);
      var done = pending;
      pending = null;  // keep the solid outline on the marked element
      setPending(null);
      done.classList.add('uim-marked');
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
      if (v) { place(savedPos()); reload(); }
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
        if (pending && choice.type === 'link' && e.target !== pending) {  // the other end of a Link
          if (linkTo) linkTo.classList.remove('uim-link');
          linkTo = e.target; linkTo.classList.add('uim-link');
          showChoice(); input.focus();
          return;
        }
        commit();
        setPending(e.target);
      }, true);
    });
    $('uim-copy').addEventListener('click', function () {
      commit();
      var open = marks.filter(actionable);
      var text = format(open);
      var btn = $('uim-copy');
      function fallback() {  // execCommand path if the async clipboard API is refused
        var ta = doc.createElement('textarea');
        ta.value = text; doc.body.appendChild(ta); ta.select();
        var ok = doc.execCommand('copy'); ta.remove();
        btn.textContent = ok ? 'Copied ' + open.length : 'Copy failed';
      }
      if (!navigator.clipboard) return fallback();
      navigator.clipboard.writeText(text).then(function () { btn.textContent = 'Copied ' + open.length; }, fallback);
    });
    $('uim-clear').addEventListener('click', function () {
      marks = []; save(marks); refresh();
      setPending(null); highlight();
      $('uim-copy').textContent = 'Copy all';
    });
    $('uim-off').addEventListener('click', function () { setOn(false); });
    return { reload: reload };
  }

  var _exports = {
    init: init, cssPath: cssPath, locate: locate, resolve: resolve, describe: describe, format: format,
    likeSelector: likeSelector, shotRect: shotRect, actionable: actionable, TYPES: TYPES,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.MarkKit = _exports;
  }
})();
