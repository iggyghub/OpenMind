'use strict';

// Self-dev pending-review card (#810, ADR-0015 amendment "in-chat
// pending-review card, human-click-only merge").
//
// Renders a `system_event` turn whose `content.kind === 'self_dev_pr_pending'`
// as a card with an "Approve & Merge" button. The click sends the
// `self_dev_pr_merge` WS message -- the ONLY place that message type is ever
// produced (cerebral/main.py's dispatcher case is the only place it's
// consumed; it is never a Tool(...) and never planner-reachable, ADR-0015
// amendment decision 3).
//
// Dual-mode: same source feeds the renderer via <script src> (window.SelfDevCard)
// and the Node tests via require(). IIFE-wrapped per the tray/lib convention
// (see action-widget.js / documents-panel.js).

(function () {
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // A PR in a terminal state never gets an offered merge button again --
  // covers both "we merged it via this card" and "merged/closed directly
  // on GitHub" (the latter is why the renderer re-checks live state instead
  // of trusting turn history alone).
  function isTerminalState(state) {
    return state === 'MERGED' || state === 'CLOSED';
  }

  function buildMergeMessage(prUrl) {
    return { type: 'self_dev_pr_merge', data: { pr_url: prUrl || '' } };
  }

  function buildStateMessage(prUrl) {
    return { type: 'self_dev_pr_state', data: { pr_url: prUrl || '' } };
  }

  // Build the card's HTML. `state` is the last known live PR state
  // ('OPEN' unless the caller already knows otherwise) -- a terminal state
  // renders with no button at all, so a stale button can never appear even
  // before the async self_dev_pr_state round trip lands.
  function renderCardHtml(turn, state) {
    var c = (turn && turn.content) || {};
    var prUrl = c.pr_url || '';
    var testBadge = c.test_passed ? 'PASS' : 'FAIL';
    var terminal = isTerminalState(state);
    var body =
      '<div class="self-dev-card-title">Self-dev PR pending review</div>' +
      '<div class="self-dev-card-row"><a href="' + escHtml(prUrl) + '" target="_blank" rel="noopener">' +
        escHtml(prUrl) + '</a></div>' +
      '<div class="self-dev-card-row">Branch: ' + escHtml(c.branch || '') + '</div>' +
      '<div class="self-dev-card-row">Reason: ' + escHtml(c.reason || '') + '</div>' +
      '<div class="self-dev-card-row">Tests: ' + testBadge + '</div>';
    if (terminal) {
      body += '<div class="self-dev-card-status">PR already ' + escHtml(String(state).toLowerCase()) + '</div>';
    } else {
      body +=
        '<button class="self-dev-card-btn" type="button">Approve &amp; Merge</button>' +
        '<div class="self-dev-card-status"></div>';
    }
    return (
      '<div class="self-dev-card" data-pr-url="' + escHtml(prUrl) +
      '" data-run-id="' + escHtml(c.run_id || '') + '">' + body + '</div>'
    );
  }

  // Wire the "Approve & Merge" click on an already-mounted card element.
  // cardEl needs getAttribute('data-pr-url') + querySelector for the button
  // and status line -- real DOM in the renderer, a minimal fake in tests.
  function attachClickHandler(cardEl, sendFn) {
    if (!cardEl || typeof cardEl.querySelector !== 'function') return;
    var btn = cardEl.querySelector('.self-dev-card-btn');
    if (!btn) return;
    var status = cardEl.querySelector('.self-dev-card-status');
    var prUrl = (cardEl.getAttribute && cardEl.getAttribute('data-pr-url')) || '';
    btn.addEventListener('click', function () {
      btn.disabled = true;
      if (status) status.textContent = 'Merging…';
      sendFn(buildMergeMessage(prUrl));
    });
  }

  function hideButton(cardEl) {
    var btn = cardEl.querySelector('.self-dev-card-btn');
    if (!btn) return;
    if (typeof btn.remove === 'function') btn.remove();
    else btn.hidden = true;
  }

  // Apply a self_dev_pr_merge_result payload to an in-place card -- success
  // removes the button and shows "Merged"; failure keeps the card actionable
  // (re-enabled button + the error message) instead of losing it.
  function applyMergeResult(cardEl, data) {
    if (!cardEl || typeof cardEl.querySelector !== 'function') return;
    var status = cardEl.querySelector('.self-dev-card-status');
    var btn = cardEl.querySelector('.self-dev-card-btn');
    data = data || {};
    if (data.status === 'merged') {
      if (status) {
        status.textContent = 'Merged' +
          (data.load_error ? ' (reload failed: ' + data.load_error + ')' : '');
      }
      hideButton(cardEl);
    } else {
      if (status) status.textContent = 'Merge failed: ' + (data.error || 'unknown error');
      if (btn) btn.disabled = false;
    }
  }

  // Apply a self_dev_pr_state_result payload -- only acts (hides the button)
  // when the live state turns out terminal; an OPEN result is a no-op.
  function applyStateResult(cardEl, data) {
    if (!cardEl || typeof cardEl.querySelector !== 'function') return;
    data = data || {};
    if (!isTerminalState(data.state)) return;
    var status = cardEl.querySelector('.self-dev-card-status');
    if (status) status.textContent = 'PR already ' + String(data.state).toLowerCase();
    hideButton(cardEl);
  }

  var _exports = {
    isTerminalState: isTerminalState,
    buildMergeMessage: buildMergeMessage,
    buildStateMessage: buildStateMessage,
    renderCardHtml: renderCardHtml,
    attachClickHandler: attachClickHandler,
    applyMergeResult: applyMergeResult,
    applyStateResult: applyStateResult,
  };

  if (typeof module === 'object' && module && module.exports) {
    module.exports = _exports;
  } else if (typeof window !== 'undefined') {
    window.SelfDevCard = _exports;
  }
})();
