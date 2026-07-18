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
