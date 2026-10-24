'use strict';

// Video trim card (VIDEO-EDIT V5, ADR-0041 decision 6).
//
// Renders a `system_event` turn whose `content.kind === 'video_trim_offer'`
// (posted by video_project's video_find_clips) as a card: the clips found,
// a comment box, and Quick trim / Vision trim / Something else. A click
// sends the choice plus the comment to the model as an ordinary typed
// message (`user_text_command`), so it lands in the chat like anything the
// user types -- no new IPC message, no window, no menu item, no hotkey.
//
// Dual-mode: same source feeds the renderer via <script src> (window.VideoTrimCard)
// and the Node tests via require(). IIFE-wrapped per the tray/lib convention
// (see self-dev-card.js).

(function () {
  var SHOWN = 8; // file names listed before "+N more"

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function baseName(p) {
    return String(p).split(/[\\/]/).pop();
  }

  // Pure: the message text a click sends. null when "Something else" has no comment.
  function buildChoiceText(choice, paths, comment) {
    var list = (paths || []).join('; ');
    var note = String(comment || '').trim();
    if (choice === 'other') return note || null; // "Something else" sends just the comment
    var verb = choice === 'vision' ? 'Vision trim' : 'Quick trim';
    return verb + ' these clips into a reel: ' + list + (note ? '\nNote: ' + note : '');
  }

  function buildChoiceMessage(choice, paths, comment) {
    var text = buildChoiceText(choice, paths, comment);
    return text == null ? null : { type: 'user_text_command', data: { text: text, attachment_ids: [] } };
  }

  function renderCardHtml(turn) {
    var c = (turn && turn.content) || {};
    var paths = c.paths || [];
    var names = paths.slice(0, SHOWN).map(function (p) {
      return '<div class="video-trim-card-row">' + escHtml(baseName(p)) + '</div>';
    }).join('');
    if (paths.length > SHOWN) {
      names += '<div class="video-trim-card-row">+' + (paths.length - SHOWN) + ' more</div>';
    }
    return (
      '<div class="video-trim-card" data-paths="' + escHtml(JSON.stringify(paths)) + '">' +
        '<div class="video-trim-card-title">' + escHtml(c.summary || (paths.length + ' clips')) + '</div>' +
        names +
        '<textarea class="video-trim-card-comment" rows="2" ' +
          'placeholder="Anything Felix should know? (optional for the trims)"></textarea>' +
        '<div class="video-trim-card-btns">' +
          '<button type="button" data-choice="quick">Quick trim</button>' +
          '<button type="button" data-choice="vision">Vision trim</button>' +
          '<button type="button" data-choice="other">Something else</button>' +
        '</div>' +
        '<div class="video-trim-card-status"></div>' +
      '</div>'
    );
  }

  // Wire the three buttons on a mounted card. One choice per card: buttons
  // disable after a send. cardEl is real DOM in the renderer, a fake in tests.
  function attachClickHandler(cardEl, sendFn) {
    if (!cardEl || typeof cardEl.querySelectorAll !== 'function') return;
    var paths = [];
    try { paths = JSON.parse(cardEl.getAttribute('data-paths') || '[]'); } catch (e) { paths = []; }
    var comment = cardEl.querySelector('.video-trim-card-comment');
    var status = cardEl.querySelector('.video-trim-card-status');
    var btns = cardEl.querySelectorAll('button[data-choice]');
    for (var i = 0; i < btns.length; i++) {
      (function (btn) {
        btn.addEventListener('click', function () {
          var choice = btn.getAttribute('data-choice');
          var msg = buildChoiceMessage(choice, paths, comment ? comment.value : '');
          if (!msg) {
            if (status) status.textContent = 'Say what you want in the box first.';
            return;
          }
          if (!sendFn(msg)) return; // not connected: keep the card usable
          for (var j = 0; j < btns.length; j++) btns[j].disabled = true;
          if (comment) comment.disabled = true;
          if (status) status.textContent = 'Sent: ' + btn.textContent;
        });
      })(btns[i]);
    }
  }

  var _exports = {
    buildChoiceText: buildChoiceText,
    buildChoiceMessage: buildChoiceMessage,
    renderCardHtml: renderCardHtml,
    attachClickHandler: attachClickHandler,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.VideoTrimCard = _exports;
  }
})();
