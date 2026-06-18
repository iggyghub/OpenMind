"""
Channel inbox store -- Issue #301 / S18.

In-RAM ring buffer of inbound channel messages (plus their auto-replies
and any manual replies the user types from the Integrations pane). The
Main window's Inbox surface renders this list grouped by session_key so
the user can see what arrived on each Telegram / Discord / WhatsApp /
Slack / Teams conversation since Cerebral last booted.

Implementer's choice (per spec): a dedicated inbox surface, NOT routing
channel messages into Conversations as threads. Reasons:

  * Routing into Conversations would require expanding the
    ``conversation_turns`` schema (channel-tagged threads, channel-aware
    auto-titling, project-aware filtering) -- out of scope for one slice.
  * A dedicated surface keeps S18 self-contained inside the existing
    Integrations pane and avoids disturbing the active Conversations
    code that S9 / S10 / S11 just landed.

The store is in-RAM only. Restart resets it. Channel transcripts remain
durable on the channel side (Telegram, Discord, etc.); persisting them
again locally would duplicate sensitive data without value for the
"what's new since I last looked" surface the UI needs.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

_DEFAULT_MAX_ENTRIES = 200


class ChannelInbox:
    """Bounded in-RAM record of channel inbound + outbound messages.

    Each entry is a dict shaped:

        {
          "id":           int,         # monotonic, UI dedupe key
          "session_key":  str,         # "telegram:12345", etc.
          "direction":    "inbound" | "outbound",
          "text":         str,
          "auto_reply":   str | None,  # inbound only -- Felix's reply
          "ts":           float,       # epoch seconds
        }
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max = max(10, max_entries)
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._max)
        self._next_id = 1

    def record_inbound(
        self,
        session_key: str,
        text: str,
        *,
        auto_reply: str | None = None,
        ts: float | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id":          self._next_id,
            "session_key": session_key,
            "direction":   "inbound",
            "text":        text,
            "auto_reply":  auto_reply,
            "ts":          ts if ts is not None else time.time(),
        }
        self._next_id += 1
        self._entries.append(entry)
        return entry

    def record_outbound(
        self,
        session_key: str,
        text: str,
        *,
        ts: float | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id":          self._next_id,
            "session_key": session_key,
            "direction":   "outbound",
            "text":        text,
            "auto_reply":  None,
            "ts":          ts if ts is not None else time.time(),
        }
        self._next_id += 1
        self._entries.append(entry)
        return entry

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()
