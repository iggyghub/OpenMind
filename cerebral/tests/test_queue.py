"""
Queue manager tests — Issue #8.

Unit tests use an in-memory SQLite DB; no disk writes, no external services.
Each test constructs a fresh QueueManager(:memory:) unless testing persistence.
"""
import pytest
from cerebral.action_queue.manager import QueueItem, QueueManager


# ── helpers ────────────────────────────────────────────────────────────────────

def _mgr() -> QueueManager:
    return QueueManager(":memory:")


# ── Slice 1: add_item creates a pending QueueItem ─────────────────────────────

def test_add_item_returns_queue_item():
    mgr = _mgr()
    item = mgr.add_item("Send email", "Felix wants to send the draft email to Bob")
    assert isinstance(item, QueueItem)


def test_add_item_status_is_pending():
    mgr = _mgr()
    item = mgr.add_item("Send email", "Draft to Bob")
    assert item.status == "pending"


def test_add_item_stores_title_and_summary():
    mgr = _mgr()
    item = mgr.add_item("Set alarm", "Alarm for 7 AM tomorrow")
    assert item.title == "Set alarm"
    assert item.summary == "Alarm for 7 AM tomorrow"


def test_add_item_assigns_unique_ids():
    mgr = _mgr()
    a = mgr.add_item("A", "a")
    b = mgr.add_item("B", "b")
    assert a.id != b.id


def test_add_item_with_tool_stores_tool_fields():
    mgr = _mgr()
    item = mgr.add_item(
        "Set alarm",
        "7 AM alarm",
        tool_name="set_alarm",
        tool_args={"time": "07:00"},
    )
    assert item.tool_name == "set_alarm"
    assert item.tool_args == {"time": "07:00"}


def test_add_item_without_tool_has_none_tool_fields():
    mgr = _mgr()
    item = mgr.add_item("Note", "Jot something down")
    assert item.tool_name is None
    assert item.tool_args is None


# ── Slice 2: get_pending returns only pending items ───────────────────────────

def test_get_pending_returns_added_items():
    mgr = _mgr()
    mgr.add_item("A", "a")
    mgr.add_item("B", "b")
    assert len(mgr.get_pending()) == 2


def test_get_pending_empty_when_no_items():
    mgr = _mgr()
    assert mgr.get_pending() == []


def test_get_pending_excludes_approved():
    mgr = _mgr()
    item = mgr.add_item("A", "a")
    mgr.approve_item(item.id)
    assert mgr.get_pending() == []


def test_get_pending_excludes_dismissed():
    mgr = _mgr()
    item = mgr.add_item("A", "a")
    mgr.dismiss_item(item.id)
    assert mgr.get_pending() == []


# ── Slice 3: approve_item marks approved and returns item ─────────────────────

def test_approve_item_returns_the_item():
    mgr = _mgr()
    added = mgr.add_item("A", "a")
    approved = mgr.approve_item(added.id)
    assert approved is not None
    assert approved.id == added.id


def test_approve_item_sets_status_approved():
    mgr = _mgr()
    item = mgr.add_item("A", "a")
    approved = mgr.approve_item(item.id)
    assert approved.status == "approved"


def test_approve_item_unknown_id_returns_none():
    mgr = _mgr()
    result = mgr.approve_item("no-such-id")
    assert result is None


# ── Slice 4: dismiss_item marks dismissed ────────────────────────────────────

def test_dismiss_item_returns_true_on_success():
    mgr = _mgr()
    item = mgr.add_item("A", "a")
    assert mgr.dismiss_item(item.id) is True


def test_dismiss_item_unknown_id_returns_false():
    mgr = _mgr()
    assert mgr.dismiss_item("no-such-id") is False


def test_dismiss_item_changes_status():
    mgr = _mgr()
    item = mgr.add_item("A", "a")
    mgr.dismiss_item(item.id)
    # Item no longer in pending; dismissal was recorded
    pending_ids = [i.id for i in mgr.get_pending()]
    assert item.id not in pending_ids


# ── Slice 5: clear_all empties the queue ─────────────────────────────────────

def test_clear_all_removes_all_items():
    mgr = _mgr()
    mgr.add_item("A", "a")
    mgr.add_item("B", "b")
    mgr.clear_all()
    assert mgr.get_pending() == []


def test_clear_all_on_empty_does_not_raise():
    mgr = _mgr()
    try:
        mgr.clear_all()
    except Exception as exc:
        pytest.fail(f"clear_all raised unexpectedly: {exc}")


# ── Slice 6: to_dict serialises for IPC ──────────────────────────────────────

def test_to_dict_has_required_ipc_fields():
    mgr = _mgr()
    item = mgr.add_item("A", "a", tool_name="get_time", tool_args={"tz": "UTC"})
    d = item.to_dict()
    assert "id" in d
    assert "title" in d
    assert "summary" in d
    assert "status" in d
    assert "tool_name" in d
    assert "tool_args" in d
    assert "created_at" in d


def test_to_dict_values_match_item():
    mgr = _mgr()
    item = mgr.add_item("Set alarm", "7 AM", tool_name="set_alarm", tool_args={"time": "07:00"})
    d = item.to_dict()
    assert d["title"] == "Set alarm"
    assert d["summary"] == "7 AM"
    assert d["tool_name"] == "set_alarm"
    assert d["tool_args"] == {"time": "07:00"}
    assert d["status"] == "pending"


# ── Slice 7: persistence across manager instances ────────────────────────────

def test_items_persist_across_manager_instances(tmp_path):
    db = tmp_path / "test.db"
    mgr1 = QueueManager(str(db))
    item = mgr1.add_item("Persistent", "survives restart")
    item_id = item.id

    mgr2 = QueueManager(str(db))
    pending = mgr2.get_pending()
    assert any(i.id == item_id for i in pending)


def test_approval_persists_across_instances(tmp_path):
    db = tmp_path / "test.db"
    mgr1 = QueueManager(str(db))
    item = mgr1.add_item("A", "a")
    mgr1.approve_item(item.id)

    mgr2 = QueueManager(str(db))
    assert mgr2.get_pending() == []
