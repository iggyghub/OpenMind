'use strict';

// Tile dialog -- a big card shown as one compact line; clicking the line morphs
// it into a centred tile over a blurred backdrop (UI cleanup mark, 2026-10-22).
//
// The card node itself is MOVED into a shared <dialog> and back, never cloned,
// so its ids, listeners and anything typed into its inputs survive: closing
// keeps the draft without submitting it. Click outside, Done or Esc closes.
// The morph is the View Transitions API (Chromium 111+): the line and the
// card share one view-transition-name across the swap.
//
// Dual-mode export (see section-collapse.js): window.TileDialog in the
// renderer, module.exports for Node tests (pure helpers only).

(function () {
  var VT = 'tile-dialog-morph';
  var dialog = null;

  function lineLabel(card) {
    var t = card.querySelector('[data-tile-title]') || card.firstElementChild;
    return t ? t.textContent.replace(/\s+/g, ' ').trim() : (card.id || 'Open');
  }

  // Runs fn inside a view transition when the browser has one.
  function morph(doc, fn) {
    if (doc.startViewTransition) doc.startViewTransition(fn);
    else fn();
  }

  function ensureDialog(doc) {
    if (dialog) return dialog;
    dialog = doc.createElement('dialog');
    dialog.className = 'tile-dialog';
    dialog.innerHTML = '<div class="tile-dialog-slot"></div>' +
      '<div class="tile-dialog-foot"><button type="button" class="tile-dialog-done">Done</button></div>';
    doc.body.appendChild(dialog);
    // A click on the dialog element itself (not its content) is the backdrop.
    dialog.addEventListener('click', function (e) { if (e.target === dialog) close(doc); });
    dialog.querySelector('.tile-dialog-done').addEventListener('click', function () { close(doc); });
    dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(doc); });  // Esc
    return dialog;
  }

  function open(doc, card) {
    var d = ensureDialog(doc);
    if (d.open) return;
    var line = card._tileLine;
    line.style.viewTransitionName = VT;
    morph(doc, function () {
      line.style.viewTransitionName = '';
      card.style.viewTransitionName = VT;
      card.hidden = false;
      d.querySelector('.tile-dialog-slot').appendChild(card);
      d._card = card;
      d.showModal();
      var first = card.querySelector('input, textarea, select');
      if (first) first.focus();
    });
  }

  function close(doc) {
    var d = dialog;
    if (!d || !d.open || !d._card) return;
    var card = d._card, line = card._tileLine;
    morph(doc, function () {
      card.style.viewTransitionName = '';
      d.close();
      card.hidden = true;
      line.after(card);          // back home, draft intact
      line.style.viewTransitionName = VT;
      d._card = null;
    });
    setTimeout(function () { line.style.viewTransitionName = ''; }, 400);
  }

  // Turns each card into a line. `statusSel` (optional) is moved from the card
  // into the line so its live status stays visible while the tile is closed.
  function enhance(cards, opts) {
    opts = opts || {};
    Array.prototype.forEach.call(cards, function (card) {
      if (card._tileLine) return;
      var doc = card.ownerDocument;
      var line = doc.createElement('button');
      line.type = 'button';
      line.className = 'tile-line';
      line.setAttribute('aria-haspopup', 'dialog');
      line.dataset.tileFor = card.id;
      var name = doc.createElement('span');
      name.className = 'tile-line-name';
      name.textContent = lineLabel(card);
      line.appendChild(name);
      var status = opts.statusSel && card.querySelector(opts.statusSel);
      if (status) line.appendChild(status);
      var chev = doc.createElement('span');
      chev.className = 'tile-line-chevron';
      chev.setAttribute('aria-hidden', 'true');
      chev.textContent = '›';
      line.appendChild(chev);
      card.before(line);
      card.hidden = true;
      card._tileLine = line;
      line.addEventListener('click', function () { open(doc, card); });
    });
  }

  // Opens the tile for a card by id (e.g. a Services "Connect" button).
  function openById(doc, id) {
    var card = doc.getElementById(id);
    if (card && card._tileLine) open(doc, card);
  }

  var _exports = { enhance: enhance, openById: openById, lineLabel: lineLabel };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.TileDialog = _exports;
  }
})();
