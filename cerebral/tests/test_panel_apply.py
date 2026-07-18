"""#417 — the panel apply/submit lane.

Live-ramp bugs (2026-07-18): a failed browser_open_session was swallowed
silently ("pressing apply does nothing"), parallel clicks fought over the
browser profile dir, and the inline-awaited jobs_apply_submit deadlocked the
websocket receive loop against the irreversible modal's confirm event.
"""
from __future__ import annotations

import asyncio

import cerebral.main as main
from cerebral.mcp.orchestrator import ToolResult


class _Recorder:
    def __init__(self, results: dict[str, ToolResult]):
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, args: dict) -> ToolResult:
        self.calls.append((name, args))
        return self.results.get(name, ToolResult(content="{}"))


def _wire(monkeypatch, rec: _Recorder, *, session_open: bool):
    notes: list[tuple[str, str]] = []
    casts: list[dict] = []

    async def notify(title, body):
        notes.append((title, body))

    async def broadcast(evt):
        casts.append(evt)

    monkeypatch.setattr(main._orc, "call_tool", rec.call_tool)
    monkeypatch.setattr(main, "_notify_user", notify)
    monkeypatch.setattr(main, "_broadcast", broadcast)
    monkeypatch.setattr(main, "_jobs_update_event", lambda: {"type": "jobs_update"})
    monkeypatch.setattr(
        main, "_get_open_browser_session",
        lambda: object() if session_open else None,
    )
    return notes, casts


async def test_apply_surfaces_open_session_failure(monkeypatch):
    rec = _Recorder({
        "browser_open_session": ToolResult(content="login expired.", is_error=True),
    })
    notes, casts = _wire(monkeypatch, rec, session_open=False)

    await main._run_panel_apply("https://ats.example/j/1")

    assert [n for n, _ in rec.calls] == ["browser_open_session"]  # never applied
    assert notes and "browser session" in notes[0][0]
    assert casts  # panel still refreshed


async def test_apply_runs_when_session_open(monkeypatch):
    rec = _Recorder({})
    notes, casts = _wire(monkeypatch, rec, session_open=True)

    await main._run_panel_apply("https://ats.example/j/2")

    assert rec.calls == [("jobs_apply_start", {"url": "https://ats.example/j/2"})]
    assert notes == []
    assert casts


async def test_apply_single_flight(monkeypatch):
    rec = _Recorder({})
    notes, _ = _wire(monkeypatch, rec, session_open=True)

    async with main._panel_jobs_lock:  # simulate an in-flight apply
        await main._run_panel_apply("https://ats.example/j/3")

    assert rec.calls == []
    assert notes and "already in progress" in notes[0][1]


class _FakeStore:
    def __init__(self, shortlist, applications=()):
        self.shortlist = shortlist
        self.applications = list(applications)
        self.status_calls: list[tuple[str, str]] = []

    def list_shortlist(self):
        return self.shortlist

    def list_applications(self):
        return self.applications

    def set_status(self, url, status):
        self.status_calls.append((url, status))


async def test_approve_all_approves_only_unapproved(monkeypatch):
    store = _FakeStore([
        {"url": "u1", "status": "new"},
        {"url": "u2", "status": "shortlisted"},
        {"url": "u3", "status": "new"},
    ])
    casts: list[dict] = []

    async def broadcast(evt):
        casts.append(evt)

    monkeypatch.setattr(main, "_job_search_store", store)
    monkeypatch.setattr(main, "_broadcast", broadcast)
    monkeypatch.setattr(main, "_jobs_update_event", lambda: {"type": "jobs_update"})

    await main._handle_message({"type": "jobs_approve_all"})

    assert store.status_calls == [("u1", "shortlisted"), ("u3", "shortlisted")]
    assert len(casts) == 1


async def test_apply_all_sequential_skips_and_submits(monkeypatch):
    """#419 — applies only to approved postings without an Application row,
    submits each ready one, counts failures, keeps going."""
    store = _FakeStore(
        shortlist=[
            {"url": "u1", "status": "shortlisted"},   # -> ready -> submitted
            {"url": "u2", "status": "new"},           # not approved: skipped
            {"url": "u3", "status": "shortlisted"},   # already applied: skipped
            {"url": "u4", "status": "shortlisted"},   # -> apply fails, run continues
        ],
        applications=[{"url": "u3", "status": "failed"}],
    )
    rec = _Recorder({})
    orig = rec.call_tool

    async def call_tool(name, args):
        if name == "jobs_apply_start" and args.get("url") == "u4":
            rec.calls.append((name, args))
            return ToolResult(content='{"status": "failed"}', is_error=True)
        return await orig(name, args)

    notes, casts = _wire(monkeypatch, rec, session_open=True)
    monkeypatch.setattr(main._orc, "call_tool", call_tool)
    monkeypatch.setattr(main, "_job_search_store", store)

    await main._run_panel_apply_all()

    assert rec.calls == [
        ("jobs_apply_start", {"url": "u1"}),
        ("jobs_apply_submit", {}),
        ("jobs_apply_start", {"url": "u4"}),
    ]
    assert notes and "1 submitted" in notes[-1][1] and "1 failed" in notes[-1][1]


async def test_apply_all_respects_batch_limit(monkeypatch):
    """#421 — at most `limit` postings per run; skips don't count."""
    store = _FakeStore(
        shortlist=[
            {"url": "u0", "status": "shortlisted"},   # already applied: skipped
            {"url": "u1", "status": "shortlisted"},
            {"url": "u2", "status": "shortlisted"},
            {"url": "u3", "status": "shortlisted"},
        ],
        applications=[{"url": "u0", "status": "failed"}],
    )
    rec = _Recorder({})
    _wire(monkeypatch, rec, session_open=True)
    monkeypatch.setattr(main, "_job_search_store", store)

    await main._run_panel_apply_all(limit=2)

    starts = [args["url"] for name, args in rec.calls if name == "jobs_apply_start"]
    assert starts == ["u1", "u2"]  # u0 skipped without consuming the batch, u3 over limit


async def test_apply_all_stops_when_modal_declined(monkeypatch):
    store = _FakeStore(shortlist=[
        {"url": "u1", "status": "shortlisted"},
        {"url": "u2", "status": "shortlisted"},
    ])
    rec = _Recorder({
        "jobs_apply_submit": ToolResult(content="denied", is_error=True),
    })
    notes, _ = _wire(monkeypatch, rec, session_open=True)
    monkeypatch.setattr(main, "_job_search_store", store)

    await main._run_panel_apply_all()

    # Declined modal on u1 stops the run — u2 is never attempted.
    assert [c for c in rec.calls if c[0] == "jobs_apply_start"] == [
        ("jobs_apply_start", {"url": "u1"}),
    ]
    assert notes and "stopped" in notes[-1][1].lower()


async def test_submit_event_does_not_block_receive_loop(monkeypatch):
    """#417 deadlock regression: the dispatcher branch must return while the
    (modal-gated) submit is still in flight."""
    release = asyncio.Event()
    calls: list[str] = []

    async def slow_call_tool(name, args):
        calls.append(name)
        await release.wait()  # parked on the "modal" until we release it
        return ToolResult(content="{}")

    monkeypatch.setattr(main._orc, "call_tool", slow_call_tool)

    casts: list[dict] = []

    async def broadcast(evt):
        casts.append(evt)

    monkeypatch.setattr(main, "_broadcast", broadcast)
    monkeypatch.setattr(main, "_jobs_update_event", lambda: {"type": "jobs_update"})

    # Must return promptly even though the tool call is parked.
    await asyncio.wait_for(
        main._handle_message({"type": "jobs_apply_submit"}), timeout=1.0
    )
    await asyncio.sleep(0)  # let the spawned task reach the tool call
    assert calls == ["jobs_apply_submit"]
    assert casts == []  # not finished yet

    release.set()
    await asyncio.sleep(0.01)
    assert casts  # task completed and refreshed the panel
