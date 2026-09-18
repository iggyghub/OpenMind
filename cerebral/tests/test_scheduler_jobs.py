"""FELIX-AUDIT S7 (F5): _run_due_event_jobs dispatches due events through a title->job table."""
from cerebral import main


class _FakeScheduler:
    def __init__(self, events):
        self._events = events
        self.marked = []
        self.list_calls = 0

    def list_due_events(self):
        self.list_calls += 1
        return list(self._events)

    def mark_event_run(self, event_id):
        self.marked.append(event_id)


def _rig(monkeypatch, events, jobs):
    sched = _FakeScheduler(events)
    monkeypatch.setattr(main, "_scheduler_plugin", sched)
    monkeypatch.setattr(main, "_due_event_jobs", lambda: jobs)
    return sched


async def test_due_event_runs_its_job_and_is_marked(monkeypatch):
    ran = []

    async def job(evt):
        ran.append(evt["id"])
        return True

    sched = _rig(monkeypatch, [{"id": 1, "title": "A"}], {"A": job})
    await main._run_due_event_jobs()
    assert ran == [1] and sched.marked == [1]


async def test_scan_is_a_single_list_due_events_call(monkeypatch):
    sched = _rig(monkeypatch, [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}], {})
    await main._run_due_event_jobs()
    assert sched.list_calls == 1


async def test_unknown_title_is_neither_run_nor_marked(monkeypatch):
    sched = _rig(monkeypatch, [{"id": 7, "title": "some-strategy"}], {"A": None})
    await main._run_due_event_jobs()
    assert sched.marked == []


async def test_job_returning_false_is_left_due(monkeypatch):
    async def job(evt):
        return False

    sched = _rig(monkeypatch, [{"id": 1, "title": "A"}], {"A": job})
    await main._run_due_event_jobs()
    assert sched.marked == []


async def test_raising_job_does_not_stop_the_others_and_is_not_marked(monkeypatch):
    ran = []

    async def boom(evt):
        raise RuntimeError("nope")

    async def ok(evt):
        ran.append(evt["id"])
        return True

    sched = _rig(
        monkeypatch,
        [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}],
        {"A": boom, "B": ok},
    )
    await main._run_due_event_jobs()
    assert ran == [2] and sched.marked == [2]


def test_real_job_table_covers_the_three_recurring_events():
    titles = set(main._due_event_jobs())
    assert titles == {
        main._ipo_calendar_plugin.IPO_CALENDAR_EVENT_TITLE,
        main._design_system_plugin.DESIGN_SYSTEM_EVENT_TITLE,
        main._discovery_plugin.DISCOVERY_EVENT_TITLE,
    }
