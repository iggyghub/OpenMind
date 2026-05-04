"""
HTTP client MCP plugin tests — Issue #24.

Tools: http_get, http_post, http_put, http_delete.

Each accepts (url, headers?, body?) and returns {status, body}.
JSON responses are parsed; non-JSON responses are returned as raw text.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_fetch(*, response_status: int = 200, response_body=None,
                response_headers: dict | None = None,
                captured: dict | None = None):
    """Build an injectable fetch_fn that records the call and returns canned data."""
    async def fake_fetch(method, url, *, headers=None, json=None):
        if captured is not None:
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
        return {
            "status": response_status,
            "body": response_body,
            "headers": response_headers or {"Content-Type": "application/json"},
        }
    return fake_fetch


def _error_fetch():
    async def fake_fetch(method, url, *, headers=None, json=None):
        raise ConnectionError("network down")
    return fake_fetch


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_four(self):
        from plugins.http_client import create

        names = {t.name for t in create().list_tools()}
        assert names == {"http_get", "http_post", "http_put", "http_delete"}

    def test_create_plugin_named_http_client(self):
        from plugins.http_client import create

        assert create().name == "http_client"


# ---------------------------------------------------------------------------
# Cycle 2 — required url arg
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["http_get", "http_post", "http_put", "http_delete"])
    async def test_missing_url_returns_error(self, tool):
        from plugins.http_client import HttpClientPlugin

        plugin = HttpClientPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool(tool, {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — http_get sends GET and returns body
# ---------------------------------------------------------------------------

class TestHttpGet:
    @pytest.mark.asyncio
    async def test_get_calls_fetch_with_get_method(self):
        from plugins.http_client import HttpClientPlugin

        captured: dict = {}
        plugin = HttpClientPlugin(
            fetch_fn=_make_fetch(
                response_status=200,
                response_body={"hello": "world"},
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "http_get", {"url": "https://api.example.com/v1/things"}
        )
        assert not result.is_error
        assert captured["method"] == "GET"
        assert captured["url"] == "https://api.example.com/v1/things"
        data = json.loads(result.content)
        assert data["status"] == 200
        assert data["body"] == {"hello": "world"}

    @pytest.mark.asyncio
    async def test_get_passes_headers(self):
        from plugins.http_client import HttpClientPlugin

        captured: dict = {}
        plugin = HttpClientPlugin(
            fetch_fn=_make_fetch(captured=captured)
        )
        await plugin.call_tool(
            "http_get",
            {
                "url": "https://api.example.com",
                "headers": {"Authorization": "Bearer abc"},
            },
        )
        assert captured["headers"] == {"Authorization": "Bearer abc"}


# ---------------------------------------------------------------------------
# Cycle 4 — http_post sends body
# ---------------------------------------------------------------------------

class TestHttpPost:
    @pytest.mark.asyncio
    async def test_post_passes_body_as_json(self):
        from plugins.http_client import HttpClientPlugin

        captured: dict = {}
        plugin = HttpClientPlugin(
            fetch_fn=_make_fetch(captured=captured),
        )
        await plugin.call_tool(
            "http_post",
            {
                "url": "https://api.example.com/things",
                "body": {"name": "thing", "active": True},
            },
        )
        assert captured["method"] == "POST"
        assert captured["json"] == {"name": "thing", "active": True}


# ---------------------------------------------------------------------------
# Cycle 5 — http_put + http_delete dispatch correct methods
# ---------------------------------------------------------------------------

class TestPutDelete:
    @pytest.mark.asyncio
    async def test_put_dispatches_put(self):
        from plugins.http_client import HttpClientPlugin

        captured: dict = {}
        plugin = HttpClientPlugin(fetch_fn=_make_fetch(captured=captured))
        await plugin.call_tool(
            "http_put", {"url": "https://api.example.com/things/1", "body": {"x": 1}}
        )
        assert captured["method"] == "PUT"

    @pytest.mark.asyncio
    async def test_delete_dispatches_delete(self):
        from plugins.http_client import HttpClientPlugin

        captured: dict = {}
        plugin = HttpClientPlugin(fetch_fn=_make_fetch(captured=captured))
        await plugin.call_tool(
            "http_delete", {"url": "https://api.example.com/things/1"}
        )
        assert captured["method"] == "DELETE"


# ---------------------------------------------------------------------------
# Cycle 6 — non-2xx response is is_error=True
# ---------------------------------------------------------------------------

class TestErrorStatus:
    @pytest.mark.asyncio
    async def test_500_response_returns_is_error(self):
        from plugins.http_client import HttpClientPlugin

        plugin = HttpClientPlugin(
            fetch_fn=_make_fetch(response_status=500, response_body="server error")
        )
        result = await plugin.call_tool(
            "http_get", {"url": "https://api.example.com/broken"}
        )
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == 500

    @pytest.mark.asyncio
    async def test_404_response_returns_is_error(self):
        from plugins.http_client import HttpClientPlugin

        plugin = HttpClientPlugin(
            fetch_fn=_make_fetch(response_status=404, response_body="not found")
        )
        result = await plugin.call_tool(
            "http_get", {"url": "https://api.example.com/missing"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — fetch_fn raises returns is_error
# ---------------------------------------------------------------------------

class TestFetchException:
    @pytest.mark.asyncio
    async def test_fetch_raises_returns_error(self):
        from plugins.http_client import HttpClientPlugin

        plugin = HttpClientPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool(
            "http_get", {"url": "https://api.example.com"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — unknown tool
# ---------------------------------------------------------------------------

class TestUnknown:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.http_client import HttpClientPlugin

        plugin = HttpClientPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("http_patch", {"url": "x"})
        assert result.is_error
