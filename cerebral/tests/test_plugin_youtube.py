"""
YouTube MCP plugin tests — Issue #109.

Tools: youtube_search, youtube_video, youtube_channel.

Stateless, read-only over the YouTube Data API v3 public-read endpoints. All
HTTP is injected via fetch_fn and the API key is injected explicitly so tests
never read the real environment or hit the network. The learning-#15
fake-transport cycle is replaced by a key-scrub cycle: YouTube's auth is a
?key= URL param built by the tool methods (not a transport header), so the
plugin-specific value to protect is the secret, not the transport.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_fetch(*, response=None, captured: dict | None = None):
    """Build an injectable fetch_fn that records calls and returns a canned dict."""
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        if captured is not None:
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            captured["json"] = json
        return response if response is not None else {}
    return fake_fetch


def _error_fetch(exc=None):
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        raise (exc or ConnectionError("network down"))
    return fake_fetch


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three(self):
        from plugins.youtube import create

        names = {t.name for t in create(api_key="k").list_tools()}
        assert names == {"youtube_search", "youtube_video", "youtube_channel"}

    def test_create_plugin_named_youtube(self):
        from plugins.youtube import create

        assert create(api_key="k").name == "youtube"

    def test_required_capabilities_overdeclare_secrets_read(self):
        from plugins.youtube import REQUIRED_CAPABILITIES

        assert REQUIRED_CAPABILITIES == frozenset(
            {"secrets_read", "external_data_read", "network_egress_cloud"}
        )

    def test_tools_have_required_args_in_schema(self):
        from plugins.youtube import create

        tools = {t.name: t for t in create(api_key="k").list_tools()}
        assert "query" in tools["youtube_search"].schema.get("required", [])
        assert "video_id" in tools["youtube_video"].schema.get("required", [])
        # channel takes exactly-one-of; neither is schema-required.
        assert tools["youtube_channel"].schema.get("required", []) == []


# ---------------------------------------------------------------------------
# Cycle 2 — unconfigured API key
# ---------------------------------------------------------------------------

class TestUnconfiguredKey:
    @pytest.mark.asyncio
    async def test_no_key_no_env_constructs_but_calls_error(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        from plugins.youtube import create

        plugin = create()  # must not raise
        for tool in ("youtube_search", "youtube_video", "youtube_channel"):
            result = await plugin.call_tool(tool, {})
            assert result.is_error
            assert "YOUTUBE_API_KEY is not configured" in result.content

    @pytest.mark.asyncio
    async def test_env_var_supplies_key(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "env-key")
        from plugins.youtube import create

        captured: dict = {}
        plugin = create(fetch_fn=_make_fetch(response={"items": []}, captured=captured))
        await plugin.call_tool("youtube_search", {"query": "x"})
        assert captured["params"]["key"] == "env-key"

    @pytest.mark.asyncio
    async def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "env-key")
        from plugins.youtube import create

        captured: dict = {}
        plugin = create(
            api_key="explicit-key",
            fetch_fn=_make_fetch(response={"items": []}, captured=captured),
        )
        await plugin.call_tool("youtube_search", {"query": "x"})
        assert captured["params"]["key"] == "explicit-key"


# ---------------------------------------------------------------------------
# Cycle 3 — required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_search_missing_query_returns_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch())
        result = await plugin.call_tool("youtube_search", {})
        assert result.is_error
        assert "'query' is required" in result.content

    @pytest.mark.asyncio
    async def test_video_missing_id_returns_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch())
        result = await plugin.call_tool("youtube_video", {})
        assert result.is_error
        assert "'video_id' is required" in result.content

    @pytest.mark.asyncio
    async def test_channel_neither_arg_returns_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch())
        result = await plugin.call_tool("youtube_channel", {})
        assert result.is_error
        assert "exactly one" in result.content

    @pytest.mark.asyncio
    async def test_channel_both_args_returns_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch())
        result = await plugin.call_tool(
            "youtube_channel", {"channel_id": "c", "handle": "@h"}
        )
        assert result.is_error
        assert "exactly one" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 — youtube_search shaping (the id-object trap)
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_search_shapes_items_and_reads_id_videoId(self):
        from plugins.youtube import YouTubePlugin

        captured: dict = {}
        payload = {"items": [{
            "id": {"kind": "youtube#video", "videoId": "abc123"},
            "snippet": {
                "title": "Hello",
                "channelTitle": "Chan",
                "publishedAt": "2026-01-01T00:00:00Z",
                "description": "desc",
            },
        }]}
        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool("youtube_search", {"query": "hello"})
        assert not result.is_error
        assert captured["method"] == "GET"
        assert captured["url"] == "https://www.googleapis.com/youtube/v3/search"
        assert captured["params"]["q"] == "hello"
        assert captured["params"]["type"] == "video"
        assert captured["params"]["part"] == "snippet"
        row = json.loads(result.content)["results"][0]
        assert row == {
            "video_id": "abc123",
            "title": "Hello",
            "channel": "Chan",
            "published_at": "2026-01-01T00:00:00Z",
            "description": "desc",
        }

    @pytest.mark.asyncio
    async def test_search_default_and_custom_max_results(self):
        from plugins.youtube import YouTubePlugin

        captured: dict = {}
        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(response={"items": []}, captured=captured),
        )
        await plugin.call_tool("youtube_search", {"query": "x"})
        assert captured["params"]["maxResults"] == 10
        await plugin.call_tool("youtube_search", {"query": "x", "max_results": 3})
        assert captured["params"]["maxResults"] == 3

    @pytest.mark.asyncio
    async def test_search_caps_results_to_max(self):
        from plugins.youtube import YouTubePlugin

        items = [
            {"id": {"videoId": f"v{i}"}, "snippet": {"title": f"t{i}"}}
            for i in range(5)
        ]
        plugin = YouTubePlugin(
            api_key="k", fetch_fn=_make_fetch(response={"items": items})
        )
        result = await plugin.call_tool(
            "youtube_search", {"query": "x", "max_results": 2}
        )
        assert len(json.loads(result.content)["results"]) == 2

    @pytest.mark.asyncio
    async def test_search_missing_snippet_fields_default_empty(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(response={"items": [{"id": {"videoId": "v"}}]}),
        )
        result = await plugin.call_tool("youtube_search", {"query": "x"})
        row = json.loads(result.content)["results"][0]
        assert row["video_id"] == "v"
        assert row["title"] == ""
        assert row["channel"] == ""

    @pytest.mark.asyncio
    async def test_search_non_dict_response_is_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch(response=[1, 2]))
        result = await plugin.call_tool("youtube_search", {"query": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — youtube_video shaping (id is a plain string; stats are strings)
# ---------------------------------------------------------------------------

class TestVideo:
    @pytest.mark.asyncio
    async def test_video_shapes_first_item_with_string_counts(self):
        from plugins.youtube import YouTubePlugin

        captured: dict = {}
        payload = {"items": [{
            "id": "vid42",
            "snippet": {
                "title": "T", "channelTitle": "C",
                "publishedAt": "2026-02-02T00:00:00Z", "description": "d",
            },
            "statistics": {
                "viewCount": "12345", "likeCount": "67", "commentCount": "8",
            },
        }]}
        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool("youtube_video", {"video_id": "vid42"})
        assert not result.is_error
        assert captured["url"] == "https://www.googleapis.com/youtube/v3/videos"
        assert captured["params"]["id"] == "vid42"
        assert captured["params"]["part"] == "snippet,statistics"
        data = json.loads(result.content)
        assert data == {
            "video_id": "vid42",
            "title": "T",
            "channel": "C",
            "published_at": "2026-02-02T00:00:00Z",
            "view_count": "12345",
            "like_count": "67",
            "comment_count": "8",
            "description": "d",
        }

    @pytest.mark.asyncio
    async def test_video_absent_like_count_defaults_empty(self):
        from plugins.youtube import YouTubePlugin

        payload = {"items": [{"id": "v", "snippet": {"title": "T"},
                              "statistics": {"viewCount": "9"}}]}
        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch(response=payload))
        result = await plugin.call_tool("youtube_video", {"video_id": "v"})
        data = json.loads(result.content)
        assert data["view_count"] == "9"
        assert data["like_count"] == ""
        assert data["comment_count"] == ""

    @pytest.mark.asyncio
    async def test_video_empty_items_is_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch(response={"items": []}))
        result = await plugin.call_tool("youtube_video", {"video_id": "nope"})
        assert result.is_error
        assert "No video found for id 'nope'" in result.content

    @pytest.mark.asyncio
    async def test_video_non_dict_response_is_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch(response="bad"))
        result = await plugin.call_tool("youtube_video", {"video_id": "v"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — youtube_channel (exactly-one-of, forHandle @-strip)
# ---------------------------------------------------------------------------

class TestChannel:
    @pytest.mark.asyncio
    async def test_channel_by_id_shapes_item(self):
        from plugins.youtube import YouTubePlugin

        captured: dict = {}
        payload = {"items": [{
            "id": "chan1",
            "snippet": {"title": "Name", "description": "about"},
            "statistics": {
                "subscriberCount": "1000", "videoCount": "20",
                "viewCount": "999999",
            },
        }]}
        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool("youtube_channel", {"channel_id": "chan1"})
        assert not result.is_error
        assert captured["url"] == "https://www.googleapis.com/youtube/v3/channels"
        assert captured["params"]["id"] == "chan1"
        assert "forHandle" not in captured["params"]
        data = json.loads(result.content)
        assert data == {
            "channel_id": "chan1",
            "title": "Name",
            "description": "about",
            "subscriber_count": "1000",
            "video_count": "20",
            "view_count": "999999",
        }

    @pytest.mark.asyncio
    async def test_channel_by_handle_strips_leading_at(self):
        from plugins.youtube import YouTubePlugin

        captured: dict = {}
        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(
                response={"items": [{"id": "c", "snippet": {"title": "n"}}]},
                captured=captured,
            ),
        )
        await plugin.call_tool("youtube_channel", {"handle": "@SomeHandle"})
        assert captured["params"]["forHandle"] == "SomeHandle"
        assert "id" not in captured["params"]

    @pytest.mark.asyncio
    async def test_channel_handle_without_at_passes_through(self):
        from plugins.youtube import YouTubePlugin

        captured: dict = {}
        plugin = YouTubePlugin(
            api_key="k",
            fetch_fn=_make_fetch(
                response={"items": [{"id": "c", "snippet": {"title": "n"}}]},
                captured=captured,
            ),
        )
        await plugin.call_tool("youtube_channel", {"handle": "PlainHandle"})
        assert captured["params"]["forHandle"] == "PlainHandle"

    @pytest.mark.asyncio
    async def test_channel_empty_items_is_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch(response={"items": []}))
        result = await plugin.call_tool("youtube_channel", {"handle": "@gone"})
        assert result.is_error
        assert "No channel found for handle '@gone'" in result.content

    @pytest.mark.asyncio
    async def test_channel_hidden_subscriber_count_defaults_empty(self):
        from plugins.youtube import YouTubePlugin

        payload = {"items": [{"id": "c", "snippet": {"title": "n"},
                              "statistics": {"videoCount": "5", "viewCount": "7"}}]}
        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch(response=payload))
        result = await plugin.call_tool("youtube_channel", {"channel_id": "c"})
        data = json.loads(result.content)
        assert data["subscriber_count"] == ""
        assert data["video_count"] == "5"


# ---------------------------------------------------------------------------
# Cycle 7 — transport errors + KEY SCRUB (learning-#15 substitution)
# ---------------------------------------------------------------------------

class TestErrorsAndKeyScrub:
    @pytest.mark.asyncio
    async def test_transport_exception_is_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_error_fetch())
        result = await plugin.call_tool("youtube_search", {"query": "x"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_api_key_never_in_toolresult_content(self):
        from plugins.youtube import YouTubePlugin

        sentinel = "SENTINEL_KEY_DO_NOT_LEAK"
        # Simulate aiohttp/httpx status errors, whose message embeds the full
        # request URL (key included).
        exc = RuntimeError(
            "403 Client Error for url: "
            f"https://www.googleapis.com/youtube/v3/search?q=x&key={sentinel}"
        )
        plugin = YouTubePlugin(api_key=sentinel, fetch_fn=_error_fetch(exc))
        for tool, args in (
            ("youtube_search", {"query": "x"}),
            ("youtube_video", {"video_id": "v"}),
            ("youtube_channel", {"channel_id": "c"}),
        ):
            result = await plugin.call_tool(tool, args)
            assert result.is_error
            assert sentinel not in result.content
            assert "***" in result.content

    @pytest.mark.asyncio
    async def test_api_key_never_in_logs(self, caplog):
        from plugins.youtube import YouTubePlugin

        sentinel = "SENTINEL_KEY_DO_NOT_LEAK"
        exc = RuntimeError(f"boom key={sentinel}")
        plugin = YouTubePlugin(api_key=sentinel, fetch_fn=_error_fetch(exc))
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("youtube_search", {"query": "x"})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 8 — dispatch + key is sent on every endpoint
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.youtube import YouTubePlugin

        plugin = YouTubePlugin(api_key="k", fetch_fn=_make_fetch())
        result = await plugin.call_tool("youtube_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'youtube_bogus'" in result.content

    @pytest.mark.asyncio
    async def test_key_is_attached_to_every_request(self):
        from plugins.youtube import YouTubePlugin

        for tool, args, resp in (
            ("youtube_search", {"query": "x"}, {"items": []}),
            ("youtube_video", {"video_id": "v"},
             {"items": [{"id": "v", "snippet": {}}]}),
            ("youtube_channel", {"channel_id": "c"},
             {"items": [{"id": "c", "snippet": {}}]}),
        ):
            captured: dict = {}
            plugin = YouTubePlugin(
                api_key="my-key",
                fetch_fn=_make_fetch(response=resp, captured=captured),
            )
            await plugin.call_tool(tool, args)
            assert captured["params"]["key"] == "my-key"

    def test_create_is_module_level_factory(self):
        from plugins.youtube import create, YouTubePlugin

        assert isinstance(create(api_key="k"), YouTubePlugin)
