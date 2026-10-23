'use strict';

// tray/lib/tile-dialog.js -- the line label. The open/close morph needs a real
// DOM + View Transitions and is verified in the browser preview.

const TileDialog = require('../lib/tile-dialog');

const card = (titleText, extra = {}) => ({
  id: 'cred-card-x',
  firstElementChild: { textContent: titleText },
  querySelector: (sel) => (sel === '[data-tile-title]' ? extra.tagged || null : null),
});

test('labels the line with the card title, whitespace collapsed', () => {
  expect(TileDialog.lineLabel(card('\n   Alpaca\n      <b>Paper</b>  '.replace(/<\/?b>/g, ''))))
    .toBe('Alpaca Paper');
});

test('an explicit [data-tile-title] wins over the first child', () => {
  expect(TileDialog.lineLabel(card('ignored', { tagged: { textContent: 'Google' } }))).toBe('Google');
});
