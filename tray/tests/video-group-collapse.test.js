'use strict';

// Guards the Videos panel collapse-preservation fix (renderVideosPanel in
// main.html). The panel re-renders wholesale every 4s poll; a native <details>
// rebuilt from the server spec ("open": gi==0) would wipe the user's manual
// toggle. renderVideosPanel captures each group's open state keyed by the
// summary's LABEL text node (firstChild) and reapplies it after the swap.
//
// The subtle part is the key: it must be the bare label, NOT the summary's full
// text -- the trailing count span ("3 clusters") changes when a cluster lands,
// so keying on textContent would drop the state on that exact tick. This test
// pins that markup contract against the real PanelSpec.renderPanel output.

const { parse } = require('node-html-parser');
const PanelSpec = require('../lib/panel-spec');

function summaryOf(html) {
  return parse(html).querySelector('.ps-group-summary');
}

// The key renderVideosPanel uses: first child (the label text node), trimmed.
function labelKey(summary) {
  const first = summary.childNodes[0];
  return first ? first.text.trim() : '';
}

test('group label is the summary first child, count is a separate span', () => {
  const s = summaryOf(PanelSpec.renderWidget({
    type: 'group', label: 'Harness improvement', count: '3 clusters', widgets: [],
  }));
  expect(labelKey(s)).toBe('Harness improvement');
  expect(s.querySelector('.ps-group-count').text).toBe('3 clusters');
});

test('label key is stable when the cluster count changes', () => {
  const three = summaryOf(PanelSpec.renderWidget({
    type: 'group', label: 'Harness improvement', count: '3 clusters', widgets: [],
  }));
  const four = summaryOf(PanelSpec.renderWidget({
    type: 'group', label: 'Harness improvement', count: '4 clusters', widgets: [],
  }));
  // Same key across a count change -> preserved collapse survives a new cluster.
  expect(labelKey(four)).toBe(labelKey(three));
  // Full text differs -> keying on textContent (the bug) would have dropped state.
  expect(four.text).not.toBe(three.text);
});

test('label key is stable when count is absent (Recent videos style)', () => {
  const s = summaryOf(PanelSpec.renderWidget({
    type: 'group', label: 'Recent videos', widgets: [],
  }));
  expect(labelKey(s)).toBe('Recent videos');
});
