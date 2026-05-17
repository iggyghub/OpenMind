"""
Insights tray IPC tests — Issue #88.

Exercises the WebSocket dispatcher branches that back the Insights tray
window: ``list_insights`` / ``delete_insight`` / ``pin_insight`` /
``edit_insight`` and the ``insights_update`` snapshot they broadcast, plus
the ``approve_item`` / ``dismiss_item`` queue branches that auto-create an
insight when a pattern crosses ``PATTERN_THRESHOLD``.

The headline regression is the dismiss path: before #88 it discarded
``maybe_create_insight``'s return and never broadcast, so an insight born
from a *dismiss* pattern never reached an open Insights window. It is now
byte-symmetric with the approve path.

Insights is SQLite, not Chroma — the rig is just an in-memory
``InsightsEngine`` + ``QueueManager``; no chromadb client (the
``test_memory_ipc.py`` PersistentClient discipline does not apply here).
"""
import pytest

from cerebral.action_queue.manager import QueueManager
from cerebral.insights.engine import InsightsEngine


@pytest.fixture
def insights_rig():
    import cerebral.main as main_mod

    eng = InsightsEngine(profile_id=1, db_path=":memory:")
    eng.PATTERN_THRESHOLD = 3
    queue = QueueManager(db_path=":memory:")

    saved = {
        "_get_insights": main_mod._get_insights,
        "_broadcast": main_mod._broadcast,
        "_queue": main_mod._queue,
    }

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._broadcast = fake_broadcast
    main_mod._get_insights = lambda: eng
    main_mod._queue = queue

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.eng = eng
            self.queue = queue
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def no_profile(self):
            main_mod._get_insights = lambda: None

        def insights_updates(self):
            return [e for e in self.sent if e["type"] == "insights_update"]

        def seed_insight(self, example, *, action="approve"):
            """Drive a pattern to threshold so an insight exists."""
            for _ in range(eng.PATTERN_THRESHOLD):
                eng.record_signal(action, example, tool_name=example)
                eng.maybe_create_insight(example, tool_name=example)
            return next(i.id for i in eng.list_insights() if i.example == example)

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── list_insights ─────────────────────────────────────────────────────────────


async def test_list_insights_broadcasts_insights_update(insights_rig):
    insights_rig.seed_insight("set_alarm")
    await insights_rig.handle({"type": "list_insights"})
    updates = insights_rig.insights_updates()
    assert len(updates) == 1
    examples = [i["example"] for i in updates[0]["data"]["insights"]]
    assert examples == ["set_alarm"]


async def test_list_insights_empty_when_no_profile(insights_rig):
    insights_rig.no_profile()
    await insights_rig.handle({"type": "list_insights"})
    updates = insights_rig.insights_updates()
    assert len(updates) == 1
    assert updates[0]["data"]["insights"] == []


# ── dismiss_item — the headline regression ────────────────────────────────────


async def test_dismiss_path_broadcasts_on_new_insight(insights_rig):
    # Two prior dismiss signals; the dispatched dismiss is the third → insight.
    for _ in range(2):
        insights_rig.eng.record_signal("dismiss", "Mute mic", tool_name="mute_mic")
    item = insights_rig.queue.add_item("Mute mic", "summary", tool_name="mute_mic")

    await insights_rig.handle({"type": "dismiss_item", "data": {"item_id": item.id}})

    updates = insights_rig.insights_updates()
    assert len(updates) == 1
    assert [i["example"] for i in updates[0]["data"]["insights"]] == ["mute_mic"]


async def test_dismiss_path_no_broadcast_below_threshold(insights_rig):
    item = insights_rig.queue.add_item("Mute mic", "summary", tool_name="mute_mic")
    await insights_rig.handle({"type": "dismiss_item", "data": {"item_id": item.id}})
    assert insights_rig.insights_updates() == []


async def test_dismiss_unknown_item_no_broadcast(insights_rig):
    await insights_rig.handle({"type": "dismiss_item", "data": {"item_id": "ghost"}})
    assert insights_rig.insights_updates() == []


# ── approve_item — parity with the dismiss path ───────────────────────────────


async def test_approve_path_broadcasts_on_new_insight(insights_rig):
    for _ in range(2):
        insights_rig.eng.record_signal("approve", "Send digest", tool_name=None)
    item = insights_rig.queue.add_item("Send digest", "summary", tool_name=None)

    await insights_rig.handle({"type": "approve_item", "data": {"item_id": item.id}})

    updates = insights_rig.insights_updates()
    assert len(updates) == 1
    assert [i["example"] for i in updates[0]["data"]["insights"]] == ["Send digest"]


# ── delete_insight ────────────────────────────────────────────────────────────


async def test_delete_insight_broadcasts_and_emits_insight_deleted(insights_rig):
    iid = insights_rig.seed_insight("set_alarm")
    await insights_rig.handle(
        {"type": "delete_insight", "data": {"insight_id": iid}}
    )
    assert len(insights_rig.insights_updates()) == 1
    assert insights_rig.insights_updates()[0]["data"]["insights"] == []
    deleted = [e for e in insights_rig.sent if e["type"] == "insight_deleted"]
    assert deleted == [{"type": "insight_deleted", "data": {"id": iid, "ok": True}}]


async def test_delete_insight_unknown_id_not_ok_no_update(insights_rig):
    insights_rig.seed_insight("set_alarm")
    await insights_rig.handle(
        {"type": "delete_insight", "data": {"insight_id": "ghost"}}
    )
    assert insights_rig.insights_updates() == []
    deleted = [e for e in insights_rig.sent if e["type"] == "insight_deleted"]
    assert deleted == [{"type": "insight_deleted", "data": {"id": "ghost", "ok": False}}]


# ── pin_insight ───────────────────────────────────────────────────────────────


async def test_pin_insight_broadcasts_insights_update(insights_rig):
    iid = insights_rig.seed_insight("set_alarm")
    await insights_rig.handle({"type": "pin_insight", "data": {"insight_id": iid}})
    updates = insights_rig.insights_updates()
    assert len(updates) == 1
    assert updates[0]["data"]["insights"][0]["pinned"] is True


async def test_pin_insight_unknown_id_no_broadcast(insights_rig):
    insights_rig.seed_insight("set_alarm")
    await insights_rig.handle({"type": "pin_insight", "data": {"insight_id": "ghost"}})
    assert insights_rig.insights_updates() == []


# ── edit_insight ──────────────────────────────────────────────────────────────


async def test_edit_insight_broadcasts_insights_update(insights_rig):
    iid = insights_rig.seed_insight("set_alarm")
    await insights_rig.handle(
        {"type": "edit_insight",
         "data": {"insight_id": iid, "description": "Custom note"}}
    )
    updates = insights_rig.insights_updates()
    assert len(updates) == 1
    assert updates[0]["data"]["insights"][0]["description"] == "Custom note"


async def test_edit_insight_blank_no_broadcast(insights_rig):
    iid = insights_rig.seed_insight("set_alarm")
    await insights_rig.handle(
        {"type": "edit_insight", "data": {"insight_id": iid, "description": "   "}}
    )
    assert insights_rig.insights_updates() == []
    assert insights_rig.eng.list_insights()[0].description.startswith("Felix often")


async def test_edit_insight_unknown_id_no_broadcast(insights_rig):
    insights_rig.seed_insight("set_alarm")
    await insights_rig.handle(
        {"type": "edit_insight",
         "data": {"insight_id": "ghost", "description": "x"}}
    )
    assert insights_rig.insights_updates() == []
