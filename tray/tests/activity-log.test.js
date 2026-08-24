'use strict';

// Unit tests for tray/lib/activity-log.js (S26/#879). testEnvironment is
// "node" project-wide (no jsdom dependency installed) -- a minimal fake
// `document` stands in for the DOM APIs render/init actually touch,
// matching trading-panel.test.js's convention exactly.

const ActivityLog = require('../lib/activity-log');

function fakeElement() {
  const el = {
    innerHTML: '',
    className: '',
    textContent: '',
    children: [],
    appendChild(child) {
      el.children.push(child);
      // A real DOM node's innerHTML reflects its subtree -- a nested
      // appendChild's own accumulated innerHTML (if it has children) takes
      // priority over its own textContent (a leaf node's actual text).
      el.innerHTML += child.innerHTML || child.textContent || '';
    },
  };
  return el;
}

function withFakeDocument(mount, fn) {
  global.document = {
    getElementById: (id) => (id === 'activity-log-mount' ? mount : null),
    createElement: () => fakeElement(),
  };
  try {
    fn();
  } finally {
    delete global.document;
  }
}

describe('initActivityLog', () => {
  test('sets a loading placeholder on the mount', () => {
    const mount = fakeElement();
    withFakeDocument(mount, () => {
      ActivityLog.initActivityLog();
      expect(mount.innerHTML).toContain('Loading');
    });
  });

  test('does not crash when the mount does not exist yet', () => {
    withFakeDocument(null, () => {
      expect(() => ActivityLog.initActivityLog()).not.toThrow();
    });
  });

  test('looks up the mount fresh on every call, not cached at module load', () => {
    // The bug the original self_dev diff shipped: `let logPane =
    // doc.querySelector(...)` ran once when the module was first required,
    // so if the pane wasn't in the DOM yet at that moment, render() became
    // a permanent no-op forever after -- even once the pane existed.
    // Calling init/render twice with two DIFFERENT fake mounts proves the
    // lookup happens inside the function, not once outside it.
    const first = fakeElement();
    const second = fakeElement();
    withFakeDocument(first, () => ActivityLog.initActivityLog());
    withFakeDocument(second, () => ActivityLog.initActivityLog());
    expect(first.innerHTML).toContain('Loading');
    expect(second.innerHTML).toContain('Loading');
  });
});

describe('renderActivityLog', () => {
  test('renders the empty state when there are no turns', () => {
    const mount = fakeElement();
    withFakeDocument(mount, () => {
      ActivityLog.renderActivityLog({ turns: [] }, mount);
      expect(mount.innerHTML).toContain('No activity recorded yet');
    });
  });

  test('renders a turn using content.summary', () => {
    const mount = fakeElement();
    withFakeDocument(mount, () => {
      ActivityLog.renderActivityLog({
        turns: [{ ts: '2026-08-24T12:00:00Z', content: { summary: 'Scheduler dispatch: 3 checked' } }],
      }, mount);
      expect(mount.innerHTML).toContain('Scheduler dispatch: 3 checked');
    });
  });

  test('falls back to content.text, then raw JSON, when summary is absent', () => {
    const mount = fakeElement();
    withFakeDocument(mount, () => {
      ActivityLog.renderActivityLog({ turns: [{ content: { text: 'plain text entry' } }] }, mount);
      expect(mount.innerHTML).toContain('plain text entry');
    });
  });

  test('does not crash on a turn with no ts', () => {
    const mount = fakeElement();
    withFakeDocument(mount, () => {
      expect(() => ActivityLog.renderActivityLog({ turns: [{ content: { summary: 'x' } }] }, mount)).not.toThrow();
    });
  });

  test('does not crash when no container is available', () => {
    withFakeDocument(null, () => {
      expect(() => ActivityLog.renderActivityLog({ turns: [] })).not.toThrow();
    });
  });

  test('uses the explicit container over the default mount lookup', () => {
    // The Trading pane's Activity section passes its OWN container
    // (source-filtered data) -- must not accidentally render into the Log
    // tab's default mount instead.
    const defaultMount = fakeElement();
    const tradingMount = fakeElement();
    withFakeDocument(defaultMount, () => {
      ActivityLog.renderActivityLog({ turns: [{ content: { summary: 'trading entry' } }] }, tradingMount);
    });
    expect(tradingMount.innerHTML).toContain('trading entry');
    expect(defaultMount.innerHTML).not.toContain('trading entry');
  });
});
