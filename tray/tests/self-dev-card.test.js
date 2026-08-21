'use strict';

// Tests for tray/lib/self-dev-card.js -- #810 (ADR-0015 amendment, in-chat
// pending-review card, human-click-only merge).
//
// No jsdom in this project (jest testEnvironment is "node"), so DOM-facing
// functions are exercised against a minimal fake element -- same technique
// as action-widget.js's initActionWidgets, taken further here since #810
// explicitly requires proving the exact click-triggered message shape.

const SelfDevCard = require('../lib/self-dev-card');

const TURN = {
  id: 42,
  kind: 'system_event',
  content: {
    kind: 'self_dev_pr_pending',
    pr_url: 'https://github.com/iggyghub/OpenMind/pull/810',
    run_id: 'abc-123',
    branch: 'selfdev/abc123',
    reason: "'plugins/self_dev.py' is a guardrail file",
    test_passed: true,
  },
};

// Minimal fake DOM: enough for querySelector('.self-dev-card-btn' | '.self-dev-card-status')
// and addEventListener/click, mirroring what the real card markup provides.
function makeFakeCard(prUrl) {
  const listeners = {};
  const btn = {
    disabled: false,
    hidden: false,
    removed: false,
    remove() { this.removed = true; },
    addEventListener(evt, fn) { listeners[evt] = fn; },
    click() { if (listeners.click) listeners.click(); },
  };
  const status = { textContent: '' };
  const card = {
    getAttribute(name) { return name === 'data-pr-url' ? prUrl : ''; },
    querySelector(sel) {
      if (sel === '.self-dev-card-btn') return btn;
      if (sel === '.self-dev-card-status') return status;
      return null;
    },
  };
  return { card, btn, status };
}

describe('isTerminalState', () => {
  test('MERGED and CLOSED are terminal', () => {
    expect(SelfDevCard.isTerminalState('MERGED')).toBe(true);
    expect(SelfDevCard.isTerminalState('CLOSED')).toBe(true);
  });
  test('OPEN and unknown values are not terminal', () => {
    expect(SelfDevCard.isTerminalState('OPEN')).toBe(false);
    expect(SelfDevCard.isTerminalState(undefined)).toBe(false);
  });
});

describe('buildMergeMessage / buildStateMessage', () => {
  test('merge message carries the exact self_dev_pr_merge shape', () => {
    expect(SelfDevCard.buildMergeMessage(TURN.content.pr_url)).toEqual({
      type: 'self_dev_pr_merge',
      data: { pr_url: TURN.content.pr_url },
    });
  });
  test('state message carries the exact self_dev_pr_state shape', () => {
    expect(SelfDevCard.buildStateMessage(TURN.content.pr_url)).toEqual({
      type: 'self_dev_pr_state',
      data: { pr_url: TURN.content.pr_url },
    });
  });
});

describe('renderCardHtml', () => {
  test('OPEN state renders the Approve & Merge button and card fields', () => {
    const html = SelfDevCard.renderCardHtml(TURN, 'OPEN');
    expect(html).toContain('self-dev-card-btn');
    expect(html).toContain('Approve &amp; Merge');
    expect(html).toContain(TURN.content.pr_url);
    expect(html).toContain(TURN.content.branch);
    expect(html).toContain('PASS');
    expect(html).toContain(`data-pr-url="${TURN.content.pr_url}"`);
  });

  test('a MERGED PR does not render the button', () => {
    const html = SelfDevCard.renderCardHtml(TURN, 'MERGED');
    expect(html).not.toContain('self-dev-card-btn');
    expect(html).toContain('already merged');
  });

  test('a CLOSED PR does not render the button', () => {
    const html = SelfDevCard.renderCardHtml(TURN, 'CLOSED');
    expect(html).not.toContain('self-dev-card-btn');
    expect(html).toContain('already closed');
  });

  test('FAIL badge when test_passed is false', () => {
    const failing = { content: Object.assign({}, TURN.content, { test_passed: false }) };
    expect(SelfDevCard.renderCardHtml(failing, 'OPEN')).toContain('FAIL');
  });

  test('escapes HTML in the reason field', () => {
    const hostile = { content: Object.assign({}, TURN.content, { reason: '<script>x</script>' }) };
    const html = SelfDevCard.renderCardHtml(hostile, 'OPEN');
    expect(html).not.toContain('<script>x</script>');
    expect(html).toContain('&lt;script&gt;');
  });
});

describe('attachClickHandler', () => {
  test('clicking sends exactly the self_dev_pr_merge message for this card\'s pr_url', () => {
    const { card, btn, status } = makeFakeCard(TURN.content.pr_url);
    const sent = [];
    SelfDevCard.attachClickHandler(card, (msg) => sent.push(msg));

    btn.click();

    expect(sent).toEqual([{
      type: 'self_dev_pr_merge',
      data: { pr_url: TURN.content.pr_url },
    }]);
    expect(btn.disabled).toBe(true);
    expect(status.textContent).toMatch(/Merging/);
  });

  test('does not throw on a card missing the button (already-terminal card)', () => {
    const card = { getAttribute: () => '', querySelector: () => null };
    expect(() => SelfDevCard.attachClickHandler(card, () => {})).not.toThrow();
  });

  test('does not throw on a null/undefined card', () => {
    expect(() => SelfDevCard.attachClickHandler(null, () => {})).not.toThrow();
    expect(() => SelfDevCard.attachClickHandler(undefined, () => {})).not.toThrow();
  });
});

describe('applyMergeResult', () => {
  test('success removes the button and shows Merged', () => {
    const { card, btn, status } = makeFakeCard(TURN.content.pr_url);
    SelfDevCard.applyMergeResult(card, { pr_url: TURN.content.pr_url, status: 'merged' });
    expect(btn.removed).toBe(true);
    expect(status.textContent).toBe('Merged');
  });

  test('success with a load_error still shows Merged plus the reload note', () => {
    const { card, status } = makeFakeCard(TURN.content.pr_url);
    SelfDevCard.applyMergeResult(card, {
      pr_url: TURN.content.pr_url, status: 'merged', load_error: 'git pull failed',
    });
    expect(status.textContent).toContain('Merged');
    expect(status.textContent).toContain('git pull failed');
  });

  test('failure keeps the card actionable: button re-enabled, error shown', () => {
    const { card, btn, status } = makeFakeCard(TURN.content.pr_url);
    btn.disabled = true; // simulate the disable done at click time
    SelfDevCard.applyMergeResult(card, {
      pr_url: TURN.content.pr_url, status: 'error', error: 'gh: not mergeable',
    });
    expect(btn.removed).toBe(false);
    expect(btn.disabled).toBe(false);
    expect(status.textContent).toContain('gh: not mergeable');
  });
});

describe('applyStateResult', () => {
  test('OPEN is a no-op -- button stays', () => {
    const { card, btn, status } = makeFakeCard(TURN.content.pr_url);
    SelfDevCard.applyStateResult(card, { pr_url: TURN.content.pr_url, state: 'OPEN' });
    expect(btn.removed).toBe(false);
    expect(status.textContent).toBe('');
  });

  test('MERGED (found via live gh check, not turn history) hides the button', () => {
    const { card, btn, status } = makeFakeCard(TURN.content.pr_url);
    SelfDevCard.applyStateResult(card, { pr_url: TURN.content.pr_url, state: 'MERGED' });
    expect(btn.removed).toBe(true);
    expect(status.textContent).toContain('already merged');
  });

  test('CLOSED hides the button', () => {
    const { card, btn } = makeFakeCard(TURN.content.pr_url);
    SelfDevCard.applyStateResult(card, { pr_url: TURN.content.pr_url, state: 'CLOSED' });
    expect(btn.removed).toBe(true);
  });
});
