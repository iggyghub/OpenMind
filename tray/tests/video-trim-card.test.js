'use strict';

// Tests for tray/lib/video-trim-card.js -- VIDEO-EDIT V5 (ADR-0041 decision 6).
// No jsdom (jest testEnvironment is "node"): the click path runs against a
// minimal fake card, as self-dev-card.test.js does.

const VideoTrimCard = require('../lib/video-trim-card');

const PATHS = ['C:\\Users\\me\\Videos\\Captures\\a.mp4', 'C:\\Users\\me\\Videos\\Captures\\b.mp4'];
const TURN = {
  id: 7,
  kind: 'system_event',
  content: { kind: 'video_trim_offer', summary: '2 clips from Captures', paths: PATHS },
};

function makeFakeCard(paths, commentText) {
  const btns = ['quick', 'vision', 'other'].map((choice) => {
    const listeners = {};
    return {
      disabled: false,
      textContent: { quick: 'Quick trim', vision: 'Vision trim', other: 'Something else' }[choice],
      getAttribute: (n) => (n === 'data-choice' ? choice : null),
      addEventListener(evt, fn) { listeners[evt] = fn; },
      click() { listeners.click(); },
    };
  });
  const comment = { value: commentText || '', disabled: false };
  const status = { textContent: '' };
  return {
    btns, comment, status,
    getAttribute: (n) => (n === 'data-paths' ? JSON.stringify(paths) : null),
    querySelector: (sel) => (sel === '.video-trim-card-comment' ? comment : sel === '.video-trim-card-status' ? status : null),
    querySelectorAll: () => btns,
  };
}

test('renderCardHtml lists the clips, a comment box and the three choices, escaped', () => {
  const html = VideoTrimCard.renderCardHtml({ content: { ...TURN.content, summary: '<b>2 clips</b>' } });
  expect(html).toContain('a.mp4');
  expect(html).toContain('b.mp4');
  expect(html).toContain('&lt;b&gt;2 clips&lt;/b&gt;');
  expect(html).toContain('video-trim-card-comment');
  expect(html).toContain('data-choice="quick"');
  expect(html).toContain('data-choice="vision"');
  expect(html).toContain('data-choice="other"');
  expect(html).not.toContain('"C:');  // data-paths is attribute-escaped
});

test('renderCardHtml shortens a long list', () => {
  const many = Array.from({ length: 11 }, (_, i) => `C:\\c\\clip${i}.mp4`);
  const html = VideoTrimCard.renderCardHtml({ content: { paths: many } });
  expect(html).toContain('clip7.mp4');
  expect(html).not.toContain('clip8.mp4</div>');
  expect(html).toContain('+3 more');
});

test('buildChoiceText carries the choice, the paths and the comment', () => {
  expect(VideoTrimCard.buildChoiceText('quick', PATHS, '')).toBe(
    'Quick trim these clips into a reel: ' + PATHS.join('; '));
  expect(VideoTrimCard.buildChoiceText('vision', PATHS, ' keep the clutch ')).toBe(
    'Vision trim these clips into a reel: ' + PATHS.join('; ') + '\nNote: keep the clutch');
  expect(VideoTrimCard.buildChoiceText('other', PATHS, ' just join them ')).toBe('just join them');
  expect(VideoTrimCard.buildChoiceText('other', PATHS, '  ')).toBeNull();
});

test('a click sends one user_text_command and locks the card', () => {
  const card = makeFakeCard(PATHS, 'name it Friday');
  const sent = [];
  VideoTrimCard.attachClickHandler(card, (m) => { sent.push(m); return true; });
  card.btns[0].click();
  expect(sent).toEqual([{ type: 'user_text_command', data: {
    text: 'Quick trim these clips into a reel: ' + PATHS.join('; ') + '\nNote: name it Friday',
    attachment_ids: [] } }]);
  expect(card.btns.every((b) => b.disabled)).toBe(true);
  expect(card.comment.disabled).toBe(true);
  expect(card.status.textContent).toBe('Sent: Quick trim');
});

test('Something else with an empty box asks for a comment and sends nothing', () => {
  const card = makeFakeCard(PATHS, '');
  const sent = [];
  VideoTrimCard.attachClickHandler(card, (m) => { sent.push(m); return true; });
  card.btns[2].click();
  expect(sent).toEqual([]);
  expect(card.btns[2].disabled).toBe(false);
  expect(card.status.textContent).toMatch(/box first/);
});

test('a failed send (disconnected) keeps the card usable', () => {
  const card = makeFakeCard(PATHS, '');
  VideoTrimCard.attachClickHandler(card, () => false);
  card.btns[1].click();
  expect(card.btns.some((b) => b.disabled)).toBe(false);
});
