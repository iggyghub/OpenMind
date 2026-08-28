"""
Time & Notes MCP plugin tests — Issue #16.

TDD vertical slices for:
  - ClockPlugin extensions: set_timer, set_reminder, list_timers
  - SchedulerPlugin: create_event, list_events, update_event, delete_event
  - NotesPlugin: create_note, search_notes, list_recent, delete_note
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest

# Ensure plugins/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ===========================================================================
# ClockPlugin — timer / reminder extensions
# ===========================================================================

class TestClockTimers:
    # -----------------------------------------------------------------------
    # Cycle 1 — set_timer queues a background task and returns id
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_set_timer_returns_id(self):
        from plugins.clock import ClockPlugin
        fired = []
        plugin = ClockPlugin(notify_fn=lambda t, m: fired.append((t, m)))
        result = await plugin.call_tool("set_timer", {"seconds": 60, "label": "pasta"})
        assert not result.is_error
        data = json.loads(result.content)
        assert "id" in data
        assert "pasta" in data.get("label", "")
        # cancel it so there's no background task dangling in the test suite
        plugin.cancel_all()

    # -----------------------------------------------------------------------
    # Cycle 2 — set_timer fires notify_fn after delay (mocked asyncio.sleep)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_set_timer_fires_notification(self):
        from plugins.clock import ClockPlugin
        fired = []
        plugin = ClockPlugin(notify_fn=lambda t, m: fired.append((t, m)))

        # Patch asyncio.sleep so the timer fires immediately
        async def instant_sleep(_):
            pass

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", instant_sleep)
            await plugin.call_tool("set_timer", {"seconds": 0.01, "label": "ping"})
            await asyncio.sleep(0)   # let background task run
            await asyncio.sleep(0)

        # Give task a moment to execute
        await asyncio.sleep(0.05)
        plugin.cancel_all()
        assert any("ping" in str(m) or "ping" in str(t) for t, m in fired)

    # -----------------------------------------------------------------------
    # Cycle 3 — set_reminder is an alias path, returns id
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_set_reminder_returns_id(self):
        from plugins.clock import ClockPlugin
        plugin = ClockPlugin(notify_fn=lambda t, m: None)
        result = await plugin.call_tool(
            "set_reminder", {"text": "check the build", "delay_seconds": 1800}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert "id" in data
        plugin.cancel_all()

    # -----------------------------------------------------------------------
    # Cycle 4 — list_timers returns active timers
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_timers_shows_active(self):
        from plugins.clock import ClockPlugin
        plugin = ClockPlugin(notify_fn=lambda t, m: None)
        await plugin.call_tool("set_timer", {"seconds": 9999, "label": "long"})
        result = await plugin.call_tool("list_timers", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "timers" in data
        assert len(data["timers"]) >= 1
        labels = [t["label"] for t in data["timers"]]
        assert "long" in labels
        plugin.cancel_all()

    # -----------------------------------------------------------------------
    # Cycle 5 — list_timers is empty when no timers queued
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_timers_empty_initially(self):
        from plugins.clock import ClockPlugin
        plugin = ClockPlugin(notify_fn=lambda t, m: None)
        result = await plugin.call_tool("list_timers", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data == {"timers": []}

    # -----------------------------------------------------------------------
    # Cycle 6 — completed timer is removed from list_timers
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_completed_timer_removed_from_list(self):
        from plugins.clock import ClockPlugin
        fired = []
        plugin = ClockPlugin(notify_fn=lambda t, m: fired.append((t, m)))

        async def instant_sleep(_):
            pass

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", instant_sleep)
            await plugin.call_tool("set_timer", {"seconds": 0.001, "label": "gone"})
        # asyncio.sleep is real again — yield so the task can run (0.001 s sleep + cleanup)
        await asyncio.sleep(0.05)

        result = await plugin.call_tool("list_timers", {})
        data = json.loads(result.content)
        labels = [t["label"] for t in data["timers"]]
        assert "gone" not in labels

    # -----------------------------------------------------------------------
    # Cycle 7 — list_tools exposes new tools
    # -----------------------------------------------------------------------
    def test_list_tools_exposes_timer_tools(self):
        from plugins.clock import create
        names = {t.name for t in create().list_tools()}
        assert {"set_timer", "set_reminder", "list_timers"}.issubset(names)

    # -----------------------------------------------------------------------
    # Cycle 8 — notify_fn defaults to logging (no crash without injection)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_no_notify_fn_does_not_crash(self):
        from plugins.clock import create
        plugin = create()  # default notify_fn
        result = await plugin.call_tool("set_timer", {"seconds": 9999, "label": "safe"})
        assert not result.is_error
        plugin.cancel_all()

    # -----------------------------------------------------------------------
    # Cycle 9 — existing get_time still works after extension
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_get_time_still_works(self):
        from plugins.clock import create
        plugin = create()
        result = await plugin.call_tool("get_time", {})
        assert not result.is_error


# ===========================================================================
# SchedulerPlugin
# ===========================================================================

class TestSchedulerPlugin:
    def _make_plugin(self, db_path=":memory:"):
        from plugins.scheduler import SchedulerPlugin
        return SchedulerPlugin(db_path=db_path)

    # -----------------------------------------------------------------------
    # Cycle 1 — create_event returns an id
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_event_returns_id(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("create_event", {
            "title": "Team standup",
            "start_iso": "2026-05-03T09:00:00",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert "id" in data
        assert isinstance(data["id"], int)

    # -----------------------------------------------------------------------
    # Cycle 2 — list_events returns created event
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_events_returns_created(self):
        plugin = self._make_plugin()
        await plugin.call_tool("create_event", {
            "title": "Morning run",
            "start_iso": "2026-05-04T07:00:00",
        })
        result = await plugin.call_tool("list_events", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "events" in data
        titles = [e["title"] for e in data["events"]]
        assert "Morning run" in titles

    # -----------------------------------------------------------------------
    # Cycle 3 — list_events filtered by date range
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_events_filtered_by_range(self):
        plugin = self._make_plugin()
        await plugin.call_tool("create_event", {
            "title": "Past event",
            "start_iso": "2025-01-01T10:00:00",
        })
        await plugin.call_tool("create_event", {
            "title": "Future event",
            "start_iso": "2027-01-01T10:00:00",
        })
        result = await plugin.call_tool("list_events", {
            "from_iso": "2026-01-01T00:00:00",
            "to_iso": "2026-12-31T23:59:59",
        })
        assert not result.is_error
        data = json.loads(result.content)
        titles = [e["title"] for e in data["events"]]
        assert "Past event" not in titles
        assert "Future event" not in titles

    # -----------------------------------------------------------------------
    # Cycle 4 — update_event changes a field
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_update_event_changes_title(self):
        plugin = self._make_plugin()
        create_result = await plugin.call_tool("create_event", {
            "title": "Old title",
            "start_iso": "2026-05-05T10:00:00",
        })
        event_id = json.loads(create_result.content)["id"]
        result = await plugin.call_tool("update_event", {
            "id": event_id, "title": "New title",
        })
        assert not result.is_error
        list_result = await plugin.call_tool("list_events", {})
        titles = [e["title"] for e in json.loads(list_result.content)["events"]]
        assert "New title" in titles
        assert "Old title" not in titles

    # -----------------------------------------------------------------------
    # Cycle 5 — update_event on missing id returns error
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_update_event_unknown_id_is_error(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("update_event", {"id": 9999, "title": "x"})
        assert result.is_error

    # -----------------------------------------------------------------------
    # Cycle 6 — delete_event removes it
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_delete_event_removes_it(self):
        plugin = self._make_plugin()
        create_result = await plugin.call_tool("create_event", {
            "title": "To delete",
            "start_iso": "2026-05-06T10:00:00",
        })
        event_id = json.loads(create_result.content)["id"]
        del_result = await plugin.call_tool("delete_event", {"id": event_id})
        assert not del_result.is_error
        list_result = await plugin.call_tool("list_events", {})
        titles = [e["title"] for e in json.loads(list_result.content)["events"]]
        assert "To delete" not in titles

    # -----------------------------------------------------------------------
    # Cycle 7 — delete_event on missing id returns error
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_delete_event_unknown_id_is_error(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("delete_event", {"id": 9999})
        assert result.is_error

    # -----------------------------------------------------------------------
    # Cycle 8 — recurrence field is stored and returned
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_event_with_recurrence(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("create_event", {
            "title": "Weekly review",
            "start_iso": "2026-05-07T10:00:00",
            "recurrence": "weekly",
        })
        assert not result.is_error
        list_result = await plugin.call_tool("list_events", {})
        events = json.loads(list_result.content)["events"]
        weekly = [e for e in events if e["title"] == "Weekly review"]
        assert len(weekly) == 1
        assert weekly[0]["recurrence"] == "weekly"

    # -----------------------------------------------------------------------
    # Cycle 9 — create_event with end_iso is stored
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_event_with_end_iso(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("create_event", {
            "title": "All day",
            "start_iso": "2026-05-08T09:00:00",
            "end_iso": "2026-05-08T17:00:00",
        })
        assert not result.is_error
        list_result = await plugin.call_tool("list_events", {})
        events = json.loads(list_result.content)["events"]
        ev = next(e for e in events if e["title"] == "All day")
        assert ev["end_iso"] == "2026-05-08T17:00:00"

    # -----------------------------------------------------------------------
    # Cycle 10 — list_tools exposes all four tools
    # -----------------------------------------------------------------------
    def test_list_tools_exposes_all_scheduler_tools(self):
        from plugins.scheduler import create
        names = {t.name for t in create().list_tools()}
        assert names == {
            "create_event", "list_events", "update_event", "delete_event",
            "run_gauntlet", "edit_strategy", "get_strategy_code", "mix_strategies",
            "run_discovery", "start_discovery", "stop_discovery", "get_discovery_status",
            "upload_book", "list_books", "stop_book", "retry_book", "resume_book", "delete_book",
            "halt_strategy", "resume_strategy",
            # S34 (#901/#906)
            "start_trading", "stop_trading",
        }

    # -----------------------------------------------------------------------
    # Cycle 11 — create() returns named plugin
    # -----------------------------------------------------------------------
    def test_create_returns_plugin_instance(self):
        from plugins.scheduler import create
        assert create().name == "scheduler"


# ===========================================================================
# NotesPlugin
# ===========================================================================

class TestNotesPlugin:
    def _make_plugin(self, tmp_path):
        from plugins.notes import NotesPlugin
        return NotesPlugin(notes_dir=str(tmp_path), db_path=":memory:")

    # -----------------------------------------------------------------------
    # Cycle 1 — create_note returns an id and writes a markdown file
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_note_returns_id(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        result = await plugin.call_tool("create_note", {
            "title": "Shopping list",
            "body": "Milk, eggs, bread",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert "id" in data

    @pytest.mark.asyncio
    async def test_create_note_writes_markdown_file(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {
            "title": "My Note",
            "body": "Hello world",
        })
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "My Note" in content
        assert "Hello world" in content

    @pytest.mark.asyncio
    async def test_create_note_has_frontmatter(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {
            "title": "FM test",
            "body": "Body text",
        })
        md_files = list(tmp_path.glob("*.md"))
        content = md_files[0].read_text(encoding="utf-8")
        assert "---" in content
        assert "title:" in content
        assert "created_at:" in content

    # -----------------------------------------------------------------------
    # Cycle 2 — list_recent returns n most-recent notes
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_recent_returns_notes(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {"title": "Note A", "body": "A"})
        await plugin.call_tool("create_note", {"title": "Note B", "body": "B"})
        result = await plugin.call_tool("list_recent", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "notes" in data
        titles = [n["title"] for n in data["notes"]]
        assert "Note A" in titles
        assert "Note B" in titles

    @pytest.mark.asyncio
    async def test_list_recent_respects_n(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        for i in range(5):
            await plugin.call_tool("create_note", {"title": f"Note {i}", "body": "x"})
        result = await plugin.call_tool("list_recent", {"n": 3})
        data = json.loads(result.content)
        assert len(data["notes"]) == 3

    # -----------------------------------------------------------------------
    # Cycle 3 — search_notes keyword match (case-insensitive)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_search_notes_finds_match(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {"title": "Recipe", "body": "Chocolate cake"})
        await plugin.call_tool("create_note", {"title": "Work", "body": "Q2 planning"})
        result = await plugin.call_tool("search_notes", {"query": "chocolate"})
        assert not result.is_error
        data = json.loads(result.content)
        titles = [n["title"] for n in data["notes"]]
        assert "Recipe" in titles
        assert "Work" not in titles

    @pytest.mark.asyncio
    async def test_search_notes_case_insensitive(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {"title": "Test", "body": "UPPER lower Mixed"})
        result = await plugin.call_tool("search_notes", {"query": "UPPER"})
        data = json.loads(result.content)
        assert len(data["notes"]) >= 1

    @pytest.mark.asyncio
    async def test_search_notes_matches_title(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {"title": "Project Phoenix", "body": "Initial ideas"})
        result = await plugin.call_tool("search_notes", {"query": "phoenix"})
        data = json.loads(result.content)
        titles = [n["title"] for n in data["notes"]]
        assert "Project Phoenix" in titles

    @pytest.mark.asyncio
    async def test_search_notes_no_match_returns_empty(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        await plugin.call_tool("create_note", {"title": "A", "body": "B"})
        result = await plugin.call_tool("search_notes", {"query": "zzznomatch"})
        data = json.loads(result.content)
        assert data["notes"] == []

    # -----------------------------------------------------------------------
    # Cycle 4 — delete_note removes the file and index entry
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_delete_note_removes_from_list(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        create_result = await plugin.call_tool("create_note", {
            "title": "Temp note",
            "body": "Delete me",
        })
        note_id = json.loads(create_result.content)["id"]
        del_result = await plugin.call_tool("delete_note", {"id": note_id})
        assert not del_result.is_error
        list_result = await plugin.call_tool("list_recent", {})
        titles = [n["title"] for n in json.loads(list_result.content)["notes"]]
        assert "Temp note" not in titles

    @pytest.mark.asyncio
    async def test_delete_note_removes_file(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        create_result = await plugin.call_tool("create_note", {
            "title": "Bye",
            "body": "Gone",
        })
        note_id = json.loads(create_result.content)["id"]
        md_files_before = list(tmp_path.glob("*.md"))
        assert len(md_files_before) == 1
        await plugin.call_tool("delete_note", {"id": note_id})
        md_files_after = list(tmp_path.glob("*.md"))
        assert len(md_files_after) == 0

    @pytest.mark.asyncio
    async def test_delete_note_unknown_id_is_error(self, tmp_path):
        plugin = self._make_plugin(tmp_path)
        result = await plugin.call_tool("delete_note", {"id": 9999})
        assert result.is_error

    # -----------------------------------------------------------------------
    # Cycle 5 — list_tools exposes four tools
    # -----------------------------------------------------------------------
    def test_list_tools_exposes_four_tools(self, tmp_path):
        from plugins.notes import create
        names = {t.name for t in create(notes_dir=str(tmp_path)).list_tools()}
        assert names == {"create_note", "search_notes", "list_recent", "delete_note"}

    # -----------------------------------------------------------------------
    # Cycle 6 — create() returns named plugin
    # -----------------------------------------------------------------------
    def test_create_returns_plugin_instance(self, tmp_path):
        from plugins.notes import create
        plugin = create(notes_dir=str(tmp_path))
        assert plugin.name == "notes"
