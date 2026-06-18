'use strict';

// Unit tests for tray/lib/channel-inbox.js -- S18 (issue #301).
// Pure data shapers: channel-name extraction, label mapping, and
// grouping inbox entries by session_key for the Integrations Inbox.

const ChannelInbox = require('../lib/channel-inbox');

// ── channelOf ───────────────────────────────────────────────────────────────

describe('channelOf', () => {
  test('extracts the channel prefix from "<channel>:<id>"', () => {
    expect(ChannelInbox.channelOf('telegram:12345')).toBe('telegram');
    expect(ChannelInbox.channelOf('discord:guild-id')).toBe('discord');
    expect(ChannelInbox.channelOf('whatsapp:+1234567890')).toBe('whatsapp');
  });

  test('returns the full key when no colon is present', () => {
    expect(ChannelInbox.channelOf('weirdkey')).toBe('weirdkey');
  });

  test('returns empty string for non-strings', () => {
    expect(ChannelInbox.channelOf(null)).toBe('');
    expect(ChannelInbox.channelOf(undefined)).toBe('');
    expect(ChannelInbox.channelOf(42)).toBe('');
  });
});

// ── channelLabel ────────────────────────────────────────────────────────────

describe('channelLabel', () => {
  test('maps known channels to canonical display labels', () => {
    expect(ChannelInbox.channelLabel('telegram')).toBe('Telegram');
    expect(ChannelInbox.channelLabel('discord')).toBe('Discord');
    expect(ChannelInbox.channelLabel('whatsapp')).toBe('WhatsApp');
    expect(ChannelInbox.channelLabel('slack')).toBe('Slack');
    expect(ChannelInbox.channelLabel('teams')).toBe('Teams');
  });

  test('passes through unknown channels (no data loss)', () => {
    expect(ChannelInbox.channelLabel('signal')).toBe('signal');
  });

  test('falls back to "Channel" on missing input', () => {
    expect(ChannelInbox.channelLabel('')).toBe('Channel');
    expect(ChannelInbox.channelLabel(undefined)).toBe('Channel');
  });
});

// ── groupBySession ──────────────────────────────────────────────────────────

describe('groupBySession', () => {
  test('returns [] for empty/missing input', () => {
    expect(ChannelInbox.groupBySession([])).toEqual([]);
    expect(ChannelInbox.groupBySession(null)).toEqual([]);
    expect(ChannelInbox.groupBySession(undefined)).toEqual([]);
  });

  test('groups entries by session_key and exposes channel metadata', () => {
    const out = ChannelInbox.groupBySession([
      { id: 1, session_key: 'telegram:a', direction: 'inbound',
        text: 'hi',  auto_reply: 'hello back', ts: 10.0 },
      { id: 2, session_key: 'telegram:a', direction: 'outbound',
        text: 'manual',  auto_reply: null,     ts: 20.0 },
    ]);
    expect(out.length).toBe(1);
    expect(out[0].session_key).toBe('telegram:a');
    expect(out[0].channel).toBe('telegram');
    expect(out[0].channel_label).toBe('Telegram');
    expect(out[0].entries.length).toBe(2);
  });

  test('places newest session first by last_ts', () => {
    const out = ChannelInbox.groupBySession([
      { id: 1, session_key: 'telegram:a', direction: 'inbound', text: 'old',  ts: 1.0 },
      { id: 2, session_key: 'discord:b',  direction: 'inbound', text: 'new',  ts: 5.0 },
    ]);
    expect(out.map(g => g.session_key)).toEqual(['discord:b', 'telegram:a']);
    expect(out[0].last_ts).toBe(5.0);
  });

  test('orders entries within a group chronologically', () => {
    const out = ChannelInbox.groupBySession([
      { id: 2, session_key: 'telegram:a', direction: 'outbound', text: 'second', ts: 20.0 },
      { id: 1, session_key: 'telegram:a', direction: 'inbound',  text: 'first',  ts: 10.0 },
      { id: 3, session_key: 'telegram:a', direction: 'outbound', text: 'third',  ts: 30.0 },
    ]);
    expect(out[0].entries.map(e => e.text)).toEqual(['first', 'second', 'third']);
  });

  test('skips malformed entries (no session_key)', () => {
    const out = ChannelInbox.groupBySession([
      { id: 1, session_key: '', text: 'lost' },
      { id: 2, session_key: 'telegram:a', direction: 'inbound', text: 'kept', ts: 1.0 },
    ]);
    expect(out.length).toBe(1);
    expect(out[0].entries.length).toBe(1);
  });
});

// ── formatTimestamp ─────────────────────────────────────────────────────────

describe('formatTimestamp', () => {
  test('returns HH:MM for a positive epoch second', () => {
    const out = ChannelInbox.formatTimestamp(1700000000); // arbitrary epoch
    expect(out).toMatch(/^\d{2}:\d{2}$/);
  });

  test('returns "" for non-positive or non-numeric input', () => {
    expect(ChannelInbox.formatTimestamp(0)).toBe('');
    expect(ChannelInbox.formatTimestamp(-1)).toBe('');
    expect(ChannelInbox.formatTimestamp(NaN)).toBe('');
    expect(ChannelInbox.formatTimestamp(undefined)).toBe('');
    expect(ChannelInbox.formatTimestamp('1234')).toBe('');
  });
});
