"""
user_notification broadcast contract (S6).

Cerebral broadcasts a {type: "user_notification", data: {title, body}} event;
the tray raises an Electron Notification (gated by notifications_enabled on the
tray side). Here we pin the event shape and that _notify_user broadcasts it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cerebral.main as main  # noqa: E402


def test_user_notification_event_shape():
    ev = main._user_notification_event("Verify it's you", "Click to finish signing in")
    assert ev == {
        "type": "user_notification",
        "data": {"title": "Verify it's you", "body": "Click to finish signing in"},
    }


async def test_notify_user_broadcasts_event(monkeypatch):
    sent = []

    async def fake_broadcast(ev):
        sent.append(ev)

    monkeypatch.setattr(main, "_broadcast", fake_broadcast)
    await main._notify_user("T", "B")
    assert sent == [
        {"type": "user_notification", "data": {"title": "T", "body": "B"}}
    ]


async def test_notify_user_noop_when_no_tray(monkeypatch):
    # _broadcast itself is a no-op when nothing is connected; _notify_user must
    # not raise in that case (best-effort).
    monkeypatch.setattr(main, "_connected", set())
    await main._notify_user("T", "B")  # should simply not raise