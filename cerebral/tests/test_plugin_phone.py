"""
Phone MCP plugin tests — Issue #23.

TDD vertical slices for PhonePlugin:
  - start_call(contact_or_number)  — POSTs to OpenClaw's outbound voice
                                      channel (e.g. /voice/dial). All voice
                                      transport happens inside OpenClaw —
                                      Cerebral is just an HTTP client.

All HTTP is injected via fetch_fn so no live OpenClaw is needed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _make_fetch(*, captured: dict | None = None,
                response: dict | None = None):
    async def fake_fetch(url, body):
        if captured is not None:
            captured["url"] = url
            captured["body"] = body
        return response or {"call_id": "call-123", "status": "ringing"}
    return fake_fetch


def _make_error_fetch():
    async def fake_fetch(url, body):
        raise ConnectionError("openclaw unreachable")
    return fake_fetch


# ---------------------------------------------------------------------------
# Cycle 1 — start_call requires contact or number
# ---------------------------------------------------------------------------

class TestStartCallRequiredArgs:
    @pytest.mark.asyncio
    async def test_start_call_missing_args_returns_error(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin(fetch_fn=_make_fetch())

        result = await plugin.call_tool("start_call", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 2 — start_call with number posts to OpenClaw's voice/dial endpoint
# ---------------------------------------------------------------------------

class TestStartCallByNumber:
    @pytest.mark.asyncio
    async def test_start_call_with_number_posts_to_voice_dial(self):
        from plugins.phone import PhonePlugin

        captured: dict = {}
        plugin = PhonePlugin(fetch_fn=_make_fetch(captured=captured))

        result = await plugin.call_tool("start_call", {"number": "+15551234567"})

        assert not result.is_error
        # Endpoint is OpenClaw's outbound voice channel
        assert captured["url"].endswith("/voice/dial")
        assert captured["body"].get("number") == "+15551234567"

    @pytest.mark.asyncio
    async def test_start_call_returns_call_id(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin(fetch_fn=_make_fetch(
            response={"call_id": "call-xyz", "status": "ringing"},
        ))

        result = await plugin.call_tool("start_call", {"number": "+15551234567"})
        data = json.loads(result.content)
        assert data["call_id"] == "call-xyz"
        assert data["status"] == "ringing"


# ---------------------------------------------------------------------------
# Cycle 3 — start_call with contact name forwards as `contact`
# ---------------------------------------------------------------------------

class TestStartCallByContact:
    @pytest.mark.asyncio
    async def test_start_call_with_contact_passes_contact(self):
        from plugins.phone import PhonePlugin

        captured: dict = {}
        plugin = PhonePlugin(fetch_fn=_make_fetch(captured=captured))

        result = await plugin.call_tool("start_call", {"contact": "Mum"})

        assert not result.is_error
        assert captured["body"].get("contact") == "Mum"


# ---------------------------------------------------------------------------
# Cycle 4 — OpenClaw failure returns is_error
# ---------------------------------------------------------------------------

class TestOpenClawFailurePropagation:
    @pytest.mark.asyncio
    async def test_openclaw_down_returns_error(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin(fetch_fn=_make_error_fetch())
        result = await plugin.call_tool("start_call", {"number": "+15551234567"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — unknown tool returns error
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — list_tools: 1 tool, correct plugin name
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_one_tool(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin()
        names = {t.name for t in plugin.list_tools()}
        assert names == {"start_call"}

    def test_list_tools_correct_plugin_name(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin()
        for tool in plugin.list_tools():
            assert tool.plugin == "phone"

    def test_list_tools_have_descriptions_and_schemas(self):
        from plugins.phone import PhonePlugin

        plugin = PhonePlugin()
        for tool in plugin.list_tools():
            assert isinstance(tool.description, str) and tool.description
            assert isinstance(tool.schema, dict) and tool.schema


# ---------------------------------------------------------------------------
# Cycle 7 — create() factory
# ---------------------------------------------------------------------------

class TestCreateFactory:
    def test_create_returns_phone_plugin(self):
        from plugins.phone import create, PhonePlugin

        plugin = create()
        assert isinstance(plugin, PhonePlugin)

    def test_create_plugin_name_is_phone(self):
        from plugins.phone import create

        assert create().name == "phone"

    def test_create_accepts_fetch_fn(self):
        from plugins.phone import create

        sentinel = _make_fetch()
        plugin = create(fetch_fn=sentinel)
        assert plugin._fetch is sentinel

    def test_create_accepts_base_url(self):
        from plugins.phone import create

        plugin = create(base_url="http://example.com:9000")
        assert plugin._base_url == "http://example.com:9000"
