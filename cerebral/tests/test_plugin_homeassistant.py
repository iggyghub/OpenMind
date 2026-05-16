"""
Home Assistant MCP plugin tests — Issue #76.

Covers HomeAssistantPlugin:
  - homeassistant_list_entities(domain?)
  - homeassistant_get_state(entity_id)
  - homeassistant_call_service(domain, service, target_entity_id?, data?)

HTTP is injected via fetch_fn; no live HA instance is required. Design decisions
locked in the issue #76 sharpener comment — do not re-litigate.

The DEVICE_CONTROL / NETWORK_EGRESS_LOCAL → SILENT gate decisions are already
covered by test_capability_gate.test_silent_class_default (parameterised). This
file pins HA-plugin-specific contracts only.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

# plugins/ lives at the repo root, alongside cerebral/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

_ENTITIES = [
    {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen"}},
    {"entity_id": "light.bedroom", "state": "on", "attributes": {"friendly_name": "Bedroom"}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
    {"entity_id": "switch.kettle", "state": "off", "attributes": {}},
]

_STATE_KITCHEN = {
    "entity_id": "light.kitchen",
    "state": "off",
    "attributes": {"friendly_name": "Kitchen"},
}


def _httpx_status_error(status: int):
    import httpx
    req = httpx.Request("GET", "http://test/api")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


def _httpx_connect_error():
    import httpx
    req = httpx.Request("GET", "http://test/api")
    return httpx.ConnectError("[Errno 111] Connection refused", request=req)


def _httpx_timeout_error():
    import httpx
    req = httpx.Request("GET", "http://test/api")
    return httpx.ReadTimeout("read timed out", request=req)


def _silent_urlopen(monkeypatch):
    """Defang the registration-time urllib ping so construction is side-effect-free."""

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b""

        def close(self):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())


# ---------------------------------------------------------------------------
# Cycle 1 — list_entities
# ---------------------------------------------------------------------------

class TestListEntities:
    @pytest.mark.asyncio
    async def test_list_entities_returns_all(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            assert method == "GET"
            assert "/api/states" in url
            return _ENTITIES

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["entities"]) == 4

    @pytest.mark.asyncio
    async def test_list_entities_filters_by_domain(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            return _ENTITIES

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_list_entities", {"domain": "light"}
        )
        assert not result.is_error
        ids = [e["entity_id"] for e in json.loads(result.content)["entities"]]
        assert set(ids) == {"light.kitchen", "light.bedroom"}

    @pytest.mark.asyncio
    async def test_list_entities_empty_result(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            return []

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert not result.is_error
        assert json.loads(result.content)["entities"] == []

    @pytest.mark.asyncio
    async def test_list_entities_sends_bearer_token(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        seen_headers = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_headers.update(headers or {})
            return []

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="my-llat")
        await plugin.call_tool("homeassistant_list_entities", {})
        assert seen_headers.get("Authorization") == "Bearer my-llat"


# ---------------------------------------------------------------------------
# Cycle 2 — get_state
# ---------------------------------------------------------------------------

class TestGetState:
    @pytest.mark.asyncio
    async def test_get_state_returns_entity_state(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            assert "/api/states/light.kitchen" in url
            return _STATE_KITCHEN

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_get_state", {"entity_id": "light.kitchen"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["entity_id"] == "light.kitchen"
        assert data["state"] == "off"

    @pytest.mark.asyncio
    async def test_get_state_missing_entity_id_arg(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        called = False

        async def fake_fetch(method, url, *, headers=None, json=None):
            nonlocal called
            called = True
            return {}

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_get_state", {})
        assert result.is_error
        assert not called

    @pytest.mark.asyncio
    async def test_get_state_unknown_entity_returns_canonical_string(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_status_error(404)

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_get_state", {"entity_id": "light.fake"}
        )
        assert result.is_error
        assert result.content == "Entity not found: 'light.fake'"


# ---------------------------------------------------------------------------
# Cycle 3 — call_service
# ---------------------------------------------------------------------------

class TestCallService:
    @pytest.mark.asyncio
    async def test_call_service_happy_path_returns_changed_list(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            assert method == "POST"
            assert "/api/services/light/turn_on" in url
            return [{"entity_id": "light.kitchen", "state": "on"}]

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_call_service",
            {"domain": "light", "service": "turn_on", "target_entity_id": "light.kitchen"},
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["changed"]) == 1
        assert "warning" not in data

    @pytest.mark.asyncio
    async def test_call_service_idempotent_empty_list_is_soft_warning(self, monkeypatch):
        """200 + [] (light already on) → is_error=False with a warning payload (sharpener §4)."""
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            return []

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_call_service",
            {"domain": "light", "service": "turn_on", "target_entity_id": "light.kitchen"},
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["changed"] == []
        assert data["warning"] == "Service ran but no entities changed"

    @pytest.mark.asyncio
    async def test_call_service_missing_required_args(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        called = False

        async def fake_fetch(method, url, *, headers=None, json=None):
            nonlocal called
            called = True
            return []

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        # No domain
        r1 = await plugin.call_tool(
            "homeassistant_call_service", {"service": "turn_on"}
        )
        # No service
        r2 = await plugin.call_tool(
            "homeassistant_call_service", {"domain": "light"}
        )
        assert r1.is_error and r2.is_error
        assert not called

    @pytest.mark.asyncio
    async def test_call_service_unknown_returns_canonical_string(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_status_error(404)

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_call_service", {"domain": "light", "service": "fake"}
        )
        assert result.is_error
        assert result.content == "Service not found: 'light.fake'"

    @pytest.mark.asyncio
    async def test_call_service_target_entity_id_merges_into_body(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        seen_body = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_body.update(json or {})
            return []

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        await plugin.call_tool(
            "homeassistant_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "target_entity_id": "light.kitchen",
                "data": {"brightness": 200},
            },
        )
        assert seen_body == {"brightness": 200, "entity_id": "light.kitchen"}

    @pytest.mark.asyncio
    async def test_call_service_without_target_does_not_inject_entity_id(self, monkeypatch):
        """target_entity_id absent → plugin does not invent an entity_id key; data wins."""
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        seen_body = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_body.update(json or {})
            return []

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        await plugin.call_tool(
            "homeassistant_call_service",
            {
                "domain": "scene",
                "service": "turn_on",
                "data": {"entity_id": "scene.evening"},
            },
        )
        assert seen_body == {"entity_id": "scene.evening"}


# ---------------------------------------------------------------------------
# Cycle 4 — missing token fail-fast (zero HTTP calls)
# ---------------------------------------------------------------------------

_MISSING_TOKEN_STR = "Set HOMEASSISTANT_TOKEN to use Home Assistant"


@pytest.mark.parametrize(
    "tool, args",
    [
        ("homeassistant_list_entities", {}),
        ("homeassistant_get_state", {"entity_id": "light.kitchen"}),
        ("homeassistant_call_service", {"domain": "light", "service": "turn_on"}),
    ],
)
@pytest.mark.asyncio
async def test_missing_token_returns_canonical_string_without_http(monkeypatch, tool, args):
    _silent_urlopen(monkeypatch)
    from plugins.homeassistant import HomeAssistantPlugin

    called = False

    async def fake_fetch(method, url, *, headers=None, json=None):
        nonlocal called
        called = True
        return {}

    plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="")
    result = await plugin.call_tool(tool, args)
    assert result.is_error
    assert result.content == _MISSING_TOKEN_STR
    assert not called


# ---------------------------------------------------------------------------
# Cycle 5 — auth / connect / generic errors (canonical strings)
# ---------------------------------------------------------------------------

class TestErrorMapping:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failure_returns_canonical_string(self, monkeypatch, status):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_status_error(status)

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert result.is_error
        assert result.content == "Home Assistant rejected the token"

    @pytest.mark.asyncio
    async def test_connect_failure_returns_canonical_string(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_connect_error()

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert result.is_error
        assert result.content == "Could not connect to Home Assistant"

    @pytest.mark.asyncio
    async def test_timeout_returns_canonical_string(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_timeout_error()

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert result.is_error
        assert result.content == "Could not connect to Home Assistant"

    @pytest.mark.asyncio
    async def test_generic_5xx_returns_canonical_string(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_status_error(500)

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert result.is_error
        assert result.content == "Home Assistant error"

    @pytest.mark.asyncio
    async def test_400_returns_generic_error(self, monkeypatch):
        """HA 400 on bad data payload — user-facing string is "Home Assistant error"
        per sharpener §7; the full body lands in the warning log only."""
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_status_error(400)

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool(
            "homeassistant_call_service",
            {"domain": "light", "service": "turn_on", "data": {"bogus": 1}},
        )
        assert result.is_error
        assert result.content == "Home Assistant error"

    @pytest.mark.asyncio
    async def test_404_on_list_entities_falls_back_to_generic(self, monkeypatch):
        """Sharpener §5: 404 on /api/states is misconfig, NOT entity-not-found."""
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise _httpx_status_error(404)

        plugin = HomeAssistantPlugin(fetch_fn=fake_fetch, token="t")
        result = await plugin.call_tool("homeassistant_list_entities", {})
        assert result.is_error
        assert result.content == "Home Assistant error"


# ---------------------------------------------------------------------------
# Cycle 6 — env vars
# ---------------------------------------------------------------------------

class TestEnvVars:
    def test_token_read_from_env(self, monkeypatch):
        monkeypatch.setenv("HOMEASSISTANT_TOKEN", "from-env")
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin()
        assert plugin._token == "from-env"

    def test_base_url_read_from_env_and_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("HOMEASSISTANT_URL", "http://192.168.1.42:8123/")
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        assert plugin._base_url == "http://192.168.1.42:8123"

    def test_token_defaults_to_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("HOMEASSISTANT_TOKEN", raising=False)
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin()
        assert plugin._token == ""

    def test_base_url_defaults_to_homeassistant_local(self, monkeypatch):
        monkeypatch.delenv("HOMEASSISTANT_URL", raising=False)
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        assert plugin._base_url == "http://homeassistant.local:8123"


# ---------------------------------------------------------------------------
# Cycle 7 — registration-time ping (sharpener §9)
# ---------------------------------------------------------------------------

class TestRegistrationPing:
    def test_registration_ping_failure_logs_warning_and_does_not_raise(
        self, monkeypatch, caplog
    ):
        from urllib.error import URLError
        from plugins.homeassistant import HomeAssistantPlugin

        def raising_urlopen(*args, **kwargs):
            raise URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", raising_urlopen)
        caplog.set_level(logging.WARNING, logger="plugins.homeassistant")

        # Construction must not propagate the URLError — plugin still registers.
        plugin = HomeAssistantPlugin(token="t", base_url="http://ha.local:8123")
        assert plugin._token == "t"
        warning_msgs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        assert any("Could not connect to Home Assistant" in m for m in warning_msgs)

    def test_registration_ping_hits_api_endpoint_with_short_timeout(
        self, monkeypatch, caplog
    ):
        from plugins.homeassistant import HomeAssistantPlugin

        called = {}

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b""

            def close(self):
                pass

        def fake_urlopen(url, *args, timeout=None, **kwargs):
            called["url"] = url
            called["timeout"] = timeout
            return _FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        caplog.set_level(logging.WARNING, logger="plugins.homeassistant")

        HomeAssistantPlugin(token="t", base_url="http://ha.local:8123")
        assert called["url"] == "http://ha.local:8123/api/"
        assert called["timeout"] == 2
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []


# ---------------------------------------------------------------------------
# Cycle 8 — tool list shape
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three_homeassistant_tools(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        names = {t.name for t in plugin.list_tools()}
        assert names == {
            "homeassistant_list_entities",
            "homeassistant_get_state",
            "homeassistant_call_service",
        }

    def test_call_service_schema_requires_domain_and_service(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        by_name = {t.name: t for t in plugin.list_tools()}
        schema = by_name["homeassistant_call_service"].schema
        assert set(schema.get("required", [])) == {"domain", "service"}
        # target_entity_id and data are optional
        props = schema.get("properties", {})
        assert "target_entity_id" in props
        assert "data" in props

    def test_get_state_schema_requires_entity_id(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        by_name = {t.name: t for t in plugin.list_tools()}
        assert by_name["homeassistant_get_state"].schema["required"] == ["entity_id"]

    def test_list_entities_schema_has_no_required_fields(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        by_name = {t.name: t for t in plugin.list_tools()}
        schema = by_name["homeassistant_list_entities"].schema
        assert "required" not in schema or schema["required"] == []

    def test_all_tools_have_plugin_homeassistant(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        for tool in plugin.list_tools():
            assert tool.plugin == "homeassistant"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(token="t")
        result = await plugin.call_tool("does_not_exist", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 9 — module constants and factory
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_plugin_name_constant(self):
        from plugins import homeassistant
        assert homeassistant.PLUGIN_NAME == "homeassistant"

    def test_required_capabilities_locked(self):
        """Sharpener §1, §3 — network_egress_local + device_control, no secrets_read."""
        from plugins import homeassistant
        assert homeassistant.REQUIRED_CAPABILITIES == frozenset({
            "network_egress_local", "device_control",
        })
        assert "secrets_read" not in homeassistant.REQUIRED_CAPABILITIES

    def test_create_returns_plugin_with_correct_name(self, monkeypatch):
        _silent_urlopen(monkeypatch)
        from plugins.homeassistant import create
        plugin = create()
        assert plugin.name == "homeassistant"


# ---------------------------------------------------------------------------
# Cycle 10 — orchestrator-side discovery & registration
# ---------------------------------------------------------------------------

class TestOrchestratorRegistration:
    @pytest.fixture
    def plugins_dir(self):
        return Path(__file__).parent.parent.parent / "plugins"

    def test_orchestrator_registers_with_locked_capabilities(self, monkeypatch, plugins_dir):
        _silent_urlopen(monkeypatch)
        from cerebral.mcp.orchestrator import MCPOrchestrator

        orc = MCPOrchestrator()
        orc.discover_plugins(plugins_dir)
        assert orc.required_capabilities_for("homeassistant") == frozenset({
            "network_egress_local", "device_control",
        })

    def test_orchestrator_marks_plugin_as_inspected(self, monkeypatch, plugins_dir):
        _silent_urlopen(monkeypatch)
        from cerebral.mcp.orchestrator import MCPOrchestrator
        from cerebral.security import INSPECTED

        orc = MCPOrchestrator()
        orc.discover_plugins(plugins_dir)
        assert orc.inspectability_for("homeassistant") == INSPECTED
