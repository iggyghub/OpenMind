"""
RSS Monitor MCP plugin tests — Issue #91.

Tools: rss_subscribe, rss_unsubscribe, rss_list_subscriptions, rss_check.

Monitoring (new-since-last-check) over a SQLite-persisted per-feed cursor.
Parsing is injected via parse_fn and the store via db_path=":memory:" so tests
are fully network-free AND state-free.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feed(entries):
    """Build a feedparser-shaped response (object with .entries)."""
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
    """parse_fn(url) -> next feed in that url's sequence.

    feeds_by_url maps url -> list of feed objects (one consumed per call;
    the last one repeats once the sequence is exhausted).
    """
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


def _make(parse_fn=None):
    from plugins.rss_monitor import RSSMonitorPlugin

    return RSSMonitorPlugin(db_path=":memory:", parse_fn=parse_fn or (lambda u: _feed([])))


async def _call(plugin, tool, args=None):
    return await plugin.call_tool(tool, args or {})


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools / create / name
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_four(self):
        from plugins.rss_monitor import create

        names = {t.name for t in create(db_path=":memory:").list_tools()}
        assert names == {
            "rss_subscribe", "rss_unsubscribe",
            "rss_list_subscriptions", "rss_check",
        }

    def test_plugin_name_is_rss_monitor(self):
        from plugins.rss_monitor import create

        assert create(db_path=":memory:").name == "rss_monitor"

    def test_required_capabilities_are_the_union(self):
        from plugins.rss_monitor import REQUIRED_CAPABILITIES

        assert REQUIRED_CAPABILITIES == frozenset({
            "external_data_read", "network_egress_cloud",
            "fs_read", "fs_write",
        })


# ---------------------------------------------------------------------------
# Cycle 2 — subscribe
# ---------------------------------------------------------------------------

class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_ok(self):
        plugin = _make()
        r = await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        assert not r.is_error
        data = json.loads(r.content)
        assert data["name"] == "BBC"
        assert data["baselined"] is False

    @pytest.mark.asyncio
    async def test_subscribe_duplicate_name_errors(self):
        plugin = _make()
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        r = await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u2"})
        assert r.is_error

    @pytest.mark.asyncio
    async def test_subscribe_requires_name_and_url(self):
        plugin = _make()
        assert (await _call(plugin, "rss_subscribe", {"url": "u"})).is_error
        assert (await _call(plugin, "rss_subscribe", {"name": "x"})).is_error

    @pytest.mark.asyncio
    async def test_subscribe_reflected_in_list(self):
        plugin = _make()
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        r = await _call(plugin, "rss_list_subscriptions")
        feeds = json.loads(r.content)["feeds"]
        assert [f["name"] for f in feeds] == ["BBC"]
        assert feeds[0]["monitoring"] is False


# ---------------------------------------------------------------------------
# Cycle 3 — unsubscribe
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribe_ok(self):
        plugin = _make()
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        r = await _call(plugin, "rss_unsubscribe", {"name": "BBC"})
        assert not r.is_error
        feeds = json.loads(
            (await _call(plugin, "rss_list_subscriptions")).content
        )["feeds"]
        assert feeds == []

    @pytest.mark.asyncio
    async def test_unsubscribe_not_found_errors(self):
        plugin = _make()
        r = await _call(plugin, "rss_unsubscribe", {"name": "Ghost"})
        assert r.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — list_subscriptions
# ---------------------------------------------------------------------------

class TestListSubscriptions:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        plugin = _make()
        r = await _call(plugin, "rss_list_subscriptions")
        assert json.loads(r.content) == {"feeds": []}

    @pytest.mark.asyncio
    async def test_monitoring_flag_flips_after_first_check(self):
        parse = _seq_parse_fn({"u": [_feed([_e("a")])]})
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=parse)
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})

        feeds = json.loads(
            (await _call(plugin, "rss_list_subscriptions")).content
        )["feeds"]
        assert feeds[0]["monitoring"] is False
        assert feeds[0]["last_checked_at"] is None

        await _call(plugin, "rss_check", {"name": "BBC"})
        feeds = json.loads(
            (await _call(plugin, "rss_list_subscriptions")).content
        )["feeds"]
        assert feeds[0]["monitoring"] is True
        assert feeds[0]["last_checked_at"] is not None


# ---------------------------------------------------------------------------
# Cycle 5 — rss_check monitoring semantics
# ---------------------------------------------------------------------------

class TestCheck:
    @pytest.mark.asyncio
    async def test_first_check_baselines_silently(self):
        parse = _seq_parse_fn({"u": [_feed([_e("c"), _e("b"), _e("a")])]})
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=parse)
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})

        r = await _call(plugin, "rss_check", {"name": "BBC"})
        res = json.loads(r.content)["results"][0]
        assert res["new"] == []
        assert res["baselined"] is True

    @pytest.mark.asyncio
    async def test_delta_after_cursor(self):
        feeds = {"u": [
            _feed([_e("a")]),                  # baseline → cursor=a
            _feed([_e("c"), _e("b"), _e("a")]),  # new: c, b
        ]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})

        await _call(plugin, "rss_check", {"name": "BBC"})  # baseline
        r = await _call(plugin, "rss_check", {"name": "BBC"})
        res = json.loads(r.content)["results"][0]
        assert [n["id"] for n in res["new"]] == ["c", "b"]

    @pytest.mark.asyncio
    async def test_no_new_when_cursor_at_head(self):
        feeds = {"u": [_feed([_e("a")]), _feed([_e("a")])]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        await _call(plugin, "rss_check", {"name": "BBC"})
        r = await _call(plugin, "rss_check", {"name": "BBC"})
        res = json.loads(r.content)["results"][0]
        assert res["new"] == []
        assert "baselined" not in res

    @pytest.mark.asyncio
    async def test_max_new_caps_burst(self):
        big = [_e(str(i)) for i in range(10)]  # newest-first 9..0
        feeds = {"u": [_feed([_e("seed")]), _feed(big + [_e("seed")])]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        await _call(plugin, "rss_check", {"name": "BBC"})
        r = await _call(plugin, "rss_check", {"name": "BBC", "max_new": 3})
        res = json.loads(r.content)["results"][0]
        assert len(res["new"]) == 3

    @pytest.mark.asyncio
    async def test_multi_feed_aggregate(self):
        feeds = {
            "ua": [_feed([_e("a1")])],
            "ub": [_feed([_e("b1")])],
        }
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "A", "url": "ua"})
        await _call(plugin, "rss_subscribe", {"name": "B", "url": "ub"})
        r = await _call(plugin, "rss_check")
        names = {res["name"] for res in json.loads(r.content)["results"]}
        assert names == {"A", "B"}

    @pytest.mark.asyncio
    async def test_check_single_feed_by_name(self):
        feeds = {"ua": [_feed([_e("a1")])], "ub": [_feed([_e("b1")])]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "A", "url": "ua"})
        await _call(plugin, "rss_subscribe", {"name": "B", "url": "ub"})
        r = await _call(plugin, "rss_check", {"name": "A"})
        results = json.loads(r.content)["results"]
        assert [res["name"] for res in results] == ["A"]

    @pytest.mark.asyncio
    async def test_check_unknown_name_errors(self):
        plugin = _make()
        r = await _call(plugin, "rss_check", {"name": "Nope"})
        assert r.is_error

    @pytest.mark.asyncio
    async def test_failed_feed_does_not_poison_others_or_advance_state(self):
        good_url, bad_url = "good", "bad"

        def parse(url):
            if url == bad_url:
                raise ConnectionError("offline")
            return _feed([_e("g1")])

        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=parse)
        await _call(plugin, "rss_subscribe", {"name": "Good", "url": good_url})
        await _call(plugin, "rss_subscribe", {"name": "Bad", "url": bad_url})

        r = await _call(plugin, "rss_check")
        by_name = {res["name"]: res for res in json.loads(r.content)["results"]}
        assert by_name["Good"]["baselined"] is True
        assert "error" in by_name["Bad"]

        # Failed feed: NEITHER cursor NOR last_checked_at advanced.
        row = plugin._con.execute(
            "SELECT last_seen_id, last_checked_at FROM rss_feeds WHERE name=?",
            ("Bad",),
        ).fetchone()
        assert row["last_seen_id"] is None
        assert row["last_checked_at"] is None

    @pytest.mark.asyncio
    async def test_entry_key_falls_back_id_then_link_then_title(self):
        # No id → link; no id/link → title.
        feeds = {"u": [
            _feed([{"title": "T1", "link": "L1", "summary": "s"}]),  # baseline by link L1
            _feed([
                {"title": "T2"},                       # key = title T2 (new)
                {"title": "T1", "link": "L1", "summary": "s"},  # key = L1 (cursor)
            ]),
        ]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        await _call(plugin, "rss_check", {"name": "BBC"})  # baseline cursor=L1
        r = await _call(plugin, "rss_check", {"name": "BBC"})
        res = json.loads(r.content)["results"][0]
        assert [n["id"] for n in res["new"]] == ["T2"]

    @pytest.mark.asyncio
    async def test_attr_shaped_entries_work(self):
        class _AttrEntry:
            def __init__(self, eid):
                self.id = eid
                self.title = "t"
                self.link = "l"
                self.summary = "s"
                self.published = "p"

        class _AttrFeed:
            def __init__(self, ids):
                self.entries = [_AttrEntry(i) for i in ids]

        feeds = {"u": [_AttrFeed(["a"]), _AttrFeed(["b", "a"])]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        await _call(plugin, "rss_check", {"name": "BBC"})
        r = await _call(plugin, "rss_check", {"name": "BBC"})
        res = json.loads(r.content)["results"][0]
        assert [n["id"] for n in res["new"]] == ["b"]

    @pytest.mark.asyncio
    async def test_empty_feed_first_check_baselines_without_cursor(self):
        feeds = {"u": [_feed([]), _feed([_e("a")])]}
        from plugins.rss_monitor import RSSMonitorPlugin
        plugin = RSSMonitorPlugin(db_path=":memory:", parse_fn=_seq_parse_fn(feeds))
        await _call(plugin, "rss_subscribe", {"name": "BBC", "url": "u"})
        r1 = await _call(plugin, "rss_check", {"name": "BBC"})
        assert json.loads(r1.content)["results"][0]["baselined"] is True
        # Cursor still NULL (nothing to baseline to) → next check re-baselines.
        r2 = await _call(plugin, "rss_check", {"name": "BBC"})
        assert json.loads(r2.content)["results"][0]["baselined"] is True


# ---------------------------------------------------------------------------
# Cycle 6 — unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = _make()
        r = await _call(plugin, "rss_search", {"q": "x"})
        assert r.is_error
