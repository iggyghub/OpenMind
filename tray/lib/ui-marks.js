'use strict';

// Mark mode -- point at the live UI and leave notes for whoever fixes it.
//
// Ctrl+Shift+M toggles it. While on, hovering outlines an element; clicking
// asks "what's wrong?" and records a mark (selector, pane, text, the styles
// that usually matter, the note). "Copy all" puts every mark on the clipboard
// as one paste. Marks survive reloads/restarts via localStorage.
//
// Dev tooling, not a Felix capability: it reads the page and writes the
// clipboard only -- no tool call, no IPC, nothing through the ADR-0005 gate.
//
// Dual-mode export (see section-collapse.js): window.UiMarks in the renderer,
// module.exports for Node tests. Pure helpers are exported for the tests.

(function () {
  var KEY = 'ui-marks';
  var STYLE_PROPS = ['font-size', 'font-weight', 'line-height', 'color', 'background-color',
    'padding', 'margin', 'gap', 'border-radius', 'width', 'height'];

  // Shortest-ish CSS path: stop at the first ancestor with an id.
  function cssPath(el) {
    var parts = [];
    while (el && el.tagName && el.tagName.toLowerCase() !== 'html') {
      if (el.id) { parts.unshift('#' + el.id); break; }
      var part = el.tagName.toLowerCase();
      var cls = el.classList ? Array.prototype.slice.call(el.classList)
        .filter(function (c) { return c.indexOf('uim-') !== 0; }) : [];
      if (cls.length) part += '.' + cls.join('.');
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

  function describe(el, computed, note) {
    var pane = el.closest ? el.closest('[data-route]') : null;
    var styles = {};
    STYLE_PROPS.forEach(function (p) {
      var v = computed.getPropertyValue(p);
      if (v && v !== 'normal' && v !== 'none' && v !== '0px' && v !== 'rgba(0, 0, 0, 0)') styles[p] = v;
    });
    return {
      pane: pane ? pane.getAttribute('data-route') : '',
      selector: cssPath(el),
      text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
      styles: styles,
      note: note,
    };
  }

  function format(marks) {
    return 'UI marks (' + marks.length + '):\n\n' + marks.map(function (m, i) {
      var s = Object.keys(m.styles).map(function (k) { return k + ': ' + m.styles[k]; }).join('; ');
      return (i + 1) + '. [' + (m.pane || '?') + '] ' + m.note + '\n' +
        '   selector: ' + m.selector + '\n' +
        (m.text ? '   text: "' + m.text + '"\n' : '') +
        '   styles: ' + s;
    }).join('\n\n');
  }

  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } }
  function save(marks) { try { localStorage.setItem(KEY, JSON.stringify(marks)); } catch (e) {} }

  // Browser-only wiring below.
  function init(doc) {
    var on = false, hovered = null, marks = load();
    var css = doc.createElement('style');
    css.textContent =
      '.uim-hover{outline:2px dashed var(--accent,#7c5cfc)!important;outline-offset:1px!important;cursor:crosshair!important}' +
      '.uim-marked{outline:2px solid var(--color-warning,#e5a50a)!important;outline-offset:1px!important}' +
      '#uim-bar{position:fixed;right:12px;bottom:12px;z-index:2147483647;display:flex;gap:6px;align-items:center;' +
      'padding:6px 10px;border-radius:var(--radius-md,10px);background:#1b1b22;border:1px solid var(--accent,#7c5cfc);' +
      'box-shadow:var(--shadow-floating);font:12px system-ui;color:#ddd}' +
      '#uim-bar[hidden]{display:none}' +
      '#uim-bar button{font:inherit;padding:2px 8px;border-radius:6px;border:1px solid #444;background:#2a2a33;color:#ddd;cursor:pointer}';
    doc.head.appendChild(css);
    var bar = doc.createElement('div');
    bar.id = 'uim-bar';
    bar.hidden = true;
    // Electron has no window.prompt(), so the note is typed into the bar itself.
    bar.innerHTML = '<span>Mark mode: <b id="uim-n">0</b></span>' +
      '<input id="uim-note" placeholder="Click something, then say what should change" hidden ' +
      'style="width:280px;font:inherit;padding:2px 6px;border-radius:6px;border:1px solid #555;background:#111;color:#eee">' +
      '<button id="uim-copy">Copy all</button><button id="uim-clear">Clear</button><button id="uim-off">Done</button>';
    doc.body.appendChild(bar);
    var count = bar.querySelector('#uim-n');
    var input = bar.querySelector('#uim-note');
    var pending = null;
    function refresh() { count.textContent = marks.length; }
    refresh();

    function setPending(el) {
      if (pending) pending.classList.remove('uim-marked');
      pending = el;
      input.hidden = !el; input.value = '';
      if (el) { el.classList.add('uim-marked'); input.focus(); }
    }
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Escape') { setPending(null); return; }
      if (e.key !== 'Enter' || !pending) return;
      var note = input.value.trim();
      if (!note) return;
      marks.push(describe(pending, doc.defaultView.getComputedStyle(pending), note));
      save(marks); refresh();
      pending = null; input.hidden = true;  // keep the solid outline on the marked element
    });

    function setOn(v) {
      on = v; bar.hidden = !v;
      if (!v) setPending(null);
      if (!v && hovered) { hovered.classList.remove('uim-hover'); hovered = null; }
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
        setPending(e.target);
      }, true);
    });
    bar.querySelector('#uim-copy').addEventListener('click', function () {
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
      Array.prototype.forEach.call(doc.querySelectorAll('.uim-marked'), function (m) { m.classList.remove('uim-marked'); });
      bar.querySelector('#uim-copy').textContent = 'Copy all';
    });
    bar.querySelector('#uim-off').addEventListener('click', function () { setOn(false); });
  }

  var _exports = { init: init, cssPath: cssPath, describe: describe, format: format };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.UiMarks = _exports;
  }
})();
