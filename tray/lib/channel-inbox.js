/* Channel inbox helpers for the S18 (#301) Integrations inbox surface.
 * Dual-mode: window.ChannelInbox in the renderer; module.exports for Node tests.
 * IIFE prevents top-level const collisions when loaded via <script src>.
 *
 * Pure data shapers -- no DOM, no IPC. The renderer maps the output of
 * groupBySession() onto HTML strings; tests assert ordering / grouping /
 * format directly on the plain-data result.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.ChannelInbox = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* The channel name embedded in a session_key, e.g. "telegram" or
   * "discord". The OpenClaw gateway emits keys shaped "<channel>:<id>".
   * If the format ever diverges, fall back to the full key. */
  function channelOf(sessionKey) {
    if (typeof sessionKey !== 'string') return '';
    var idx = sessionKey.indexOf(':');
    if (idx <= 0) return sessionKey;
    return sessionKey.slice(0, idx);
  }

  /* Display label for a channel name. Capitalises and prettifies known
   * channels; passes through unknown ones so the UI still renders them. */
  var CHANNEL_LABELS = {
    telegram: 'Telegram',
    discord:  'Discord',
    whatsapp: 'WhatsApp',
    slack:    'Slack',
    teams:    'Teams',
  };
  function channelLabel(channel) {
    var key = String(channel || '').toLowerCase();
    if (CHANNEL_LABELS[key]) return CHANNEL_LABELS[key];
    return channel ? String(channel) : 'Channel';
  }

  /* Group inbox entries by session_key, newest session first (by the most
   * recent entry's ts), entries inside each group in chronological order
   * (oldest first -- a transcript reads top-down).
   *
   * Input shape (mirrors cerebral/channel_inbox.py):
   *   { id, session_key, direction: 'inbound'|'outbound',
   *     text, auto_reply, ts }
   *
   * Output shape:
   *   [{ session_key, channel, channel_label, last_ts, entries: [...] }, ...]
   */
  function groupBySession(entries) {
    if (!Array.isArray(entries) || entries.length === 0) return [];
    var byKey = Object.create(null);
    var order = [];
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      if (!e || typeof e.session_key !== 'string' || !e.session_key) continue;
      var key = e.session_key;
      if (!byKey[key]) {
        byKey[key] = {
          session_key:   key,
          channel:       channelOf(key),
          channel_label: channelLabel(channelOf(key)),
          last_ts:       e.ts || 0,
          entries:       [],
        };
        order.push(key);
      }
      byKey[key].entries.push(e);
      if ((e.ts || 0) > byKey[key].last_ts) byKey[key].last_ts = e.ts || 0;
    }
    var groups = order.map(function (k) { return byKey[k]; });
    /* Chronological inside each group. */
    for (var j = 0; j < groups.length; j++) {
      groups[j].entries.sort(function (a, b) {
        return (a.ts || 0) - (b.ts || 0);
      });
    }
    /* Newest session first overall. */
    groups.sort(function (a, b) { return b.last_ts - a.last_ts; });
    return groups;
  }

  /* Format a UNIX timestamp (seconds) as a short HH:MM string. Anything
   * non-numeric returns ''. Tests pass a known epoch so the output is
   * deterministic. */
  function formatTimestamp(ts) {
    if (typeof ts !== 'number' || !isFinite(ts) || ts <= 0) return '';
    var d = new Date(ts * 1000);
    var hh = String(d.getHours()).padStart(2, '0');
    var mm = String(d.getMinutes()).padStart(2, '0');
    return hh + ':' + mm;
  }

  return {
    channelOf:       channelOf,
    channelLabel:    channelLabel,
    groupBySession:  groupBySession,
    formatTimestamp: formatTimestamp,
    CHANNEL_LABELS:  CHANNEL_LABELS,
  };
}));
