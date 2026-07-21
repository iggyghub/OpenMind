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


# ── Slice 8: risky flag from title (Issue #52) ───────────────────────────────

def test_add_item_with_risky_verb_sets_risky_true():
    mgr = _mgr()
    item = mgr.add_item("Send John a message", "phone plugin send")
    assert item.risky is True


def test_add_item_without_risky_verb_sets_risky_false():
    mgr = _mgr()
    item = mgr.add_item("Read my notes", "notes plugin read")
    assert item.risky is False


def test_add_item_risky_defaults_false_when_title_is_empty():
    mgr = _mgr()
    item = mgr.add_item("", "empty title")
    assert item.risky is False


def test_to_dict_includes_risky_field():
    mgr = _mgr()
    item = mgr.add_item("Send the email", "draft to Bob")
    d = item.to_dict()
    assert "risky" in d
    assert d["risky"] is True


def test_get_pending_preserves_risky_flag_across_restart(tmp_path):
    # Sharpener #6 in #52 claimed the queue is RAM-only — it's actually
    # SQLite-backed. The `risky` flag is computed on read in _row_to_item
    # so existing rows gain the badge after restart without a migration.
    db = tmp_path / "test.db"
    mgr1 = QueueManager(str(db))
    mgr1.add_item("Send the email", "summary")
    mgr1.add_item("Read my notes", "summary")

    mgr2 = QueueManager(str(db))
    pending = mgr2.get_pending()
    risk_by_title = {p.title: p.risky for p in pending}
    assert risk_by_title["Send the email"] is True
    assert risk_by_title["Read my notes"] is False


def test_risky_recomputed_on_read_for_existing_rows(tmp_path):
    # If a future change rewords the verb list, existing queued rows
    # immediately reflect the new vocabulary — derived, not stored.
    db = tmp_path / "test.db"
    mgr = QueueManager(str(db))
    item = mgr.add_item("Delete the old logs", "cleanup")
    assert item.risky is True
    # Verify the get_pending read also computes it.
    pending = mgr.get_pending()
    assert pending[0].risky is True


# ── Slice 9: kind column (ADR-0013 decision 1) ───────────────────────────────


def test_add_item_defaults_kind_to_candidate_action():
    mgr = _mgr()
    item = mgr.add_item("A", "a")
    assert item.kind == "candidate_action"


def test_add_item_accepts_kind():
    mgr = _mgr()
    item = mgr.add_item("Remember this", "durable fact", kind="memory_proposal")
    assert item.kind == "memory_proposal"


def test_to_dict_includes_kind():
    mgr = _mgr()
    item = mgr.add_item("A", "a", kind="recipe_proposal")
    assert item.to_dict()["kind"] == "recipe_proposal"


def test_kind_persists_across_manager_instances(tmp_path):
    db = tmp_path / "test.db"
    mgr1 = QueueManager(str(db))
    mgr1.add_item("A", "a", kind="memory_proposal")
    mgr2 = QueueManager(str(db))
    kinds = [p.kind for p in mgr2.get_pending()]
    assert kinds == ["memory_proposal"]


def test_migration_adds_kind_to_preexisting_row(tmp_path):
    """Live DB has 311 dismissed rows written before the kind column existed.
    The additive ALTER must fill them with the default without data loss."""
    import sqlite3
    db = tmp_path / "test.db"
    # Simulate the pre-S7 schema and insert a row.
    pre = sqlite3.connect(str(db))
    pre.executescript("""
        CREATE TABLE queue (
            id          TEXT    PRIMARY KEY,
            title       TEXT    NOT NULL,
            summary     TEXT    NOT NULL DEFAULT '',
            status      TEXT    NOT NULL DEFAULT 'pending',
            tool_name   TEXT,
            tool_args   TEXT,
            created_at  TEXT    NOT NULL
        );
        INSERT INTO queue (id, title, summary, status, tool_name, tool_args, created_at)
        VALUES ('legacy-1', 'Discord DM from iggyphi', 's', 'dismissed', NULL, NULL, '2026-01-01T00:00:00+00:00');
    """)
    pre.commit()
    pre.close()

    # Boot the manager -- migration runs.
    mgr = QueueManager(str(db))
    row = mgr._con.execute("SELECT title, kind FROM queue WHERE id='legacy-1'").fetchone()
    assert row["title"] == "Discord DM from iggyphi"
    assert row["kind"] == "candidate_action"

    # Idempotency: a second boot must not raise.
    mgr2 = QueueManager(str(db))
    row2 = mgr2._con.execute("SELECT kind FROM queue WHERE id='legacy-1'").fetchone()
    assert row2["kind"] == "candidate_action"
