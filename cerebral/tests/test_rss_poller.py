"""
Background RSS poller tests — Issue #94.

Exercises ``_rss_poll_interval`` (env parsing/clamping), ``_rss_poll_once``
(the single producer cycle) and ``_rss_poll_loop`` (the _shutdown-aware
loop). None of these functions had any prior coverage — this file is the
first pin of the producer-only posture (ADR-0005 "liberal queue, strict
execution": new entries become passive queue items with tool_name=None,
nothing auto-executes).

Cerebral-core slice: no plugins/*.py is added or modified, so there is
**zero +3 parametrize-over-plugins fan-out**. Rig mirrors
test_memory_injection.py (save/patch/restore cerebral.main attrs) crossed
with test_plugin_rss_monitor.py's network-free _feed/_seq_parse_fn harness.
The store is a real RSSMonitorPlugin(db_path=":memory:") registered in a
real MCPOrchestrator — SQLite :memory:, no chromadb (learning #10). No
asyncio.run in any sync body (asyncio_mode = auto; learning #7).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cerebral.action_queue.manager import QueueManager
from cerebral.mcp.orchestrator import MCPOrchestrator, ToolResult


# ── feedparser-shaped helpers (mirrors test_plugin_rss_monitor.py) ─────────────

def _feed(entries):
    class _Entry(dict):
        def __getattr__(self, k):
            try:
                return self[k]
            except KeyError as exc:
                raise AttributeError(k) from exc

    class _Feed:
        def __init__(self):
            self.entries = [_Entry(e) for e in entries]

    return _Feed()


def _seq_parse_fn(feeds_by_url):
    """parse_fn(url) -> next feed in that url's sequence (last repeats)."""
    state = {u: 0 for u in feeds_by_url}

    def fake_parse(url):
        if url not in feeds_by_url:
            raise ValueError(f"unexpected url: {url}")
        seq = feeds_by_url[url]
        i = min(state[url], len(seq) - 1)
        state[url] += 1
        return seq[i]

    return fake_parse


def _e(eid, title="t", link="l", summary="s", published="2026-05-17"):
    return {"id": eid, "title": title, "link": link, "summary": summary,
            "published": published}


# ── Rig ───────────────────────────────────────────────────────────────────────

class _FakeOrc:
    """Stands in for the orchestrator when forcing rss_check's failure
    shapes (is_error / bad-JSON / raises) which a real plugin won't emit."""

    def __init__(self, *, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = 0

    async def call_tool(self, name, args, capability=None, flags=None):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest.fixture
def rig(monkeypatch):
    import cerebral.main as main_mod

    orc = MCPOrchestrator()
    queue = QueueManager(":memory:")
    broadcasts: list[dict] = []

    async def _capture(event):
        broadcasts.append(event)

    monkeypatch.setattr(main_mod, "_orc", orc)
    monkeypatch.setattr(main_mod, "_queue", queue)
    monkeypatch.setattr(main_mod, "_broadcast", _capture)
    # The production _shutdown is a single module-level Event bound to
    # main()'s one loop. pytest-asyncio gives each test its own loop, so a
    # shared Event raised "bound to a different event loop" across loop
    # tests. A fresh per-test Event (unbound until first .wait()) isolates
    # them without changing production behaviour.
    monkeypatch.setattr(main_mod, "_shutdown", asyncio.Event())

    class Rig:
        module = main_mod
        orchestrator = orc

        def __init__(self):
            self.queue = queue
            self.broadcasts = broadcasts

        def register_feeds(self, feeds_by_url):
            from plugins.rss_monitor import RSSMonitorPlugin
            plugin = RSSMonitorPlugin(
                db_path=":memory:", parse_fn=_seq_parse_fn(feeds_by_url)
            )
            orc.register(plugin)
            self.plugin = plugin
            return plugin

        async def subscribe(self, name, url):
            return await self.plugin.call_tool(
                "rss_subscribe", {"name": name, "url": url}
            )

        def use_fake_orc(self, **kw):
            fake = _FakeOrc(**kw)
            monkeypatch.setattr(main_mod, "_orc", fake)
            return fake

        def pending_titles(self):
            return [i.title for i in self.queue.get_pending()]

    return Rig()


# ── _rss_poll_interval ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, None),       # unset
    ("0", None),        # zero disables
    ("-5", None),       # negative disables
    ("abc", None),      # non-int disables
    ("", None),         # empty disables (int("") raises ValueError)
    ("30", 60),         # below floor → clamped up
    ("60", 60),         # exactly the floor
    ("900", 900),       # normal
])
def test_rss_poll_interval(rig, monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("RSS_POLL_INTERVAL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("RSS_POLL_INTERVAL_SECONDS", raw)
    assert rig.module._rss_poll_interval() == expected


# ── _rss_poll_once: producer behaviour ────────────────────────────────────────

async def test_first_poll_baselines_silently_no_queue_items(rig):
    rig.register_feeds({"u1": [_feed([_e("a1", title="A1")])]})
    await rig.subscribe("tech", "u1")
    await rig.module._rss_poll_once()
    # First check baselines to the newest entry — nothing surfaced.
    assert rig.pending_titles() == []
    assert rig.broadcasts == []


async def test_new_entry_becomes_passive_queue_item(rig):
    rig.register_feeds({"u1": [
        _feed([_e("a1", title="A1")]),                       # baseline
        _feed([_e("a2", title="A2"), _e("a1", title="A1")]),  # a2 is new
    ]})
    await rig.subscribe("tech", "u1")
    await rig.module._rss_poll_once()   # baseline
    await rig.module._rss_poll_once()   # delta → A2
    items = rig.queue.get_pending()
    assert [i.title for i in items] == ["A2"]
    # Producer-only: queued as a passive notification candidate, never an
    # executable (tool_name=None) — ADR-0005 strict-execution holds.
    assert items[0].tool_name is None
    assert items[0].summary == "tech — l"
    assert rig.broadcasts and rig.broadcasts[-1]["type"] == "queue_update"


async def test_cursor_advances_no_repeat_surface(rig):
    rig.register_feeds({"u1": [
        _feed([_e("a1")]),
        _feed([_e("a2"), _e("a1")]),
        _feed([_e("a2"), _e("a1")]),  # nothing newer than a2
    ]})
    await rig.subscribe("tech", "u1")
    await rig.module._rss_poll_once()  # baseline
    await rig.module._rss_poll_once()  # a2 surfaces
    await rig.module._rss_poll_once()  # cursor at a2 → no repeat
    assert len(rig.queue.get_pending()) == 1


async def test_manual_rss_check_after_poll_returns_empty(rig):
    """The poll loop and a manual rss_check share the per-feed cursor —
    after a poll surfaces an entry, a manual check returns no new entries."""
    rig.register_feeds({"u1": [
        _feed([_e("a1")]),
        _feed([_e("a2"), _e("a1")]),
    ]})
    await rig.subscribe("tech", "u1")
    await rig.module._rss_poll_once()  # baseline
    await rig.module._rss_poll_once()  # a2 surfaced to the queue
    res = await rig.plugin.call_tool("rss_check", {})
    payload = json.loads(res.content)
    assert payload["results"][0]["new"] == []


async def test_no_subscriptions_is_silent_noop(rig):
    rig.register_feeds({})
    await rig.module._rss_poll_once()
    assert rig.pending_titles() == []
    assert rig.broadcasts == []


async def test_empty_title_falls_back_to_feed_label(rig):
    rig.register_feeds({"u1": [
        _feed([_e("a1")]),
        _feed([_e("a2", title=""), _e("a1")]),
    ]})
    await rig.subscribe("news", "u1")
    await rig.module._rss_poll_once()
    await rig.module._rss_poll_once()
    assert rig.pending_titles() == ["news update"]


async def test_entry_without_url_summary_is_feed_name(rig):
    rig.register_feeds({"u1": [
        _feed([_e("a1")]),
        _feed([_e("a2", title="T2", link=""), _e("a1")]),
    ]})
    await rig.subscribe("news", "u1")
    await rig.module._rss_poll_once()
    await rig.module._rss_poll_once()
    assert rig.queue.get_pending()[0].summary == "news"


async def test_multi_feed_surfaces_all(rig):
    rig.register_feeds({
        "u1": [_feed([_e("a1")]), _feed([_e("a2", title="A2"), _e("a1")])],
        "u2": [_feed([_e("b1")]), _feed([_e("b2", title="B2"), _e("b1")])],
    })
    await rig.subscribe("alpha", "u1")
    await rig.subscribe("beta", "u2")
    await rig.module._rss_poll_once()
    await rig.module._rss_poll_once()
    assert sorted(rig.pending_titles()) == ["A2", "B2"]


# ── _rss_poll_once: failure degradation (loop must survive) ────────────────────

async def test_rss_check_is_error_degrades_silently(rig):
    rig.use_fake_orc(result=ToolResult(content="boom", is_error=True))
    await rig.module._rss_poll_once()  # must not raise
    assert rig.pending_titles() == []
    assert rig.broadcasts == []


async def test_rss_check_bad_json_degrades_silently(rig):
    rig.use_fake_orc(result=ToolResult(content="not json{", is_error=False))
    await rig.module._rss_poll_once()
    assert rig.pending_titles() == []


async def test_rss_check_raises_degrades_silently(rig):
    rig.use_fake_orc(exc=RuntimeError("orchestrator exploded"))
    await rig.module._rss_poll_once()
    assert rig.pending_titles() == []


# ── _rss_poll_loop: _shutdown-aware lifecycle ─────────────────────────────────

async def test_loop_exits_promptly_on_shutdown(rig):
    task = asyncio.create_task(rig.module._rss_poll_loop(3600))
    await asyncio.sleep(0)            # let it reach the wait_for
    rig.module._shutdown.set()       # signal shutdown
    await asyncio.wait_for(task, timeout=1.0)  # exits without a poll
    assert rig.pending_titles() == []


async def test_loop_polls_after_interval_then_stops(rig):
    rig.register_feeds({"u1": [
        _feed([_e("a1")]),
        _feed([_e("a2", title="A2"), _e("a1")]),
    ]})
    await rig.subscribe("tech", "u1")
    await rig.module._rss_poll_once()  # baseline outside the loop
    task = asyncio.create_task(rig.module._rss_poll_loop(0.01))
    await asyncio.sleep(0.05)          # ≥ one interval → one poll fires
    rig.module._shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert rig.pending_titles() == ["A2"]
