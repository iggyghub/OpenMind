"""
Google Maps MCP plugin tests -- Issue #226.

Tools: maps_geocode, maps_reverse_geocode, maps_place_search, maps_directions.

Static-key posture (youtube.py precedent): auth is a ``?key=`` query parameter.
All HTTP is injected via ``fetch_fn`` and the API key is injected via a
constructor-injected ``TokenProvider`` stub. No real network, keyring, or
browser is needed in this suite.
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

class _StubProvider:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def current(self) -> str | None:
        return self._token


def _make_fetch(*, response=None, captured: dict | None = None):
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
# Cycle 1 -- list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_four(self):
        from plugins.google_maps import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "maps_geocode",
            "maps_reverse_geocode",
            "maps_place_search",
            "maps_directions",
        }

    def test_create_plugin_named_google_maps(self):
        from plugins.google_maps import create

        assert create().name == "google_maps"

    def test_required_capabilities_overdeclare_secrets_read(self):
        from plugins.google_maps import REQUIRED_CAPABILITIES

        assert REQUIRED_CAPABILITIES == frozenset(
            {"secrets_read", "external_data_read", "network_egress_cloud"}
        )

    def test_tools_have_required_args_in_schema(self):
        from plugins.google_maps import create

        tools = {t.name: t for t in create().list_tools()}
        assert "address" in tools["maps_geocode"].schema.get("required", [])
        assert "lat" in tools["maps_reverse_geocode"].schema.get("required", [])
        assert "lng" in tools["maps_reverse_geocode"].schema.get("required", [])
        assert "query" in tools["maps_place_search"].schema.get("required", [])
        assert "origin" in tools["maps_directions"].schema.get("required", [])
        assert "destination" in tools["maps_directions"].schema.get("required", [])


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no token + setter seam
# ---------------------------------------------------------------------------

class TestNoToken:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.google_maps as mod
        monkeypatch.setattr(mod, "_token_provider_factory", None)

        plugin = mod.create()
        for tool, args in (
            ("maps_geocode", {"address": "London"}),
            ("maps_reverse_geocode", {"lat": 51.5, "lng": -0.1}),
            ("maps_place_search", {"query": "coffee"}),
            ("maps_directions", {"origin": "A", "destination": "B"}),
        ):
            result = await plugin.call_tool(tool, args)
            assert result.is_error
            assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_token(self, monkeypatch):
        import plugins.google_maps as mod
        monkeypatch.setattr(mod, "_token_provider_factory", lambda: None)

        plugin = mod.create()
        result = await plugin.call_tool("maps_geocode", {"address": "London"})
        assert result.is_error
        assert "GOOGLE_MAPS_API_KEY is not configured" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.google_maps as mod
        monkeypatch.setattr(mod, "_token_provider_factory", None)

        prov = _StubProvider("seam-token")
        mod.set_token_provider(lambda: prov)
        try:
            captured: dict = {}
            plugin = mod.GoogleMapsPlugin(
                fetch_fn=_make_fetch(
                    response={"status": "OK", "results": []}, captured=captured
                )
            )
            await plugin.call_tool("maps_geocode", {"address": "test"})
            assert captured["params"]["key"] == "seam-token"
        finally:
            monkeypatch.setattr(mod, "_token_provider_factory", None)

    @pytest.mark.asyncio
    async def test_constructor_provider_wins_over_module_factory(self, monkeypatch):
        import plugins.google_maps as mod
        monkeypatch.setattr(
            mod, "_token_provider_factory",
            lambda: _StubProvider("from-module-factory"),
        )

        captured: dict = {}
        plugin = mod.GoogleMapsPlugin(
            token_provider=_StubProvider("from-constructor"),
            fetch_fn=_make_fetch(
                response={"status": "OK", "results": []}, captured=captured
            ),
        )
        await plugin.call_tool("maps_geocode", {"address": "test"})
        assert captured["params"]["key"] == "from-constructor"


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_geocode_missing_address_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_geocode", {})
        assert result.is_error
        assert "'address' is required" in result.content

    @pytest.mark.asyncio
    async def test_reverse_geocode_missing_lat_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_reverse_geocode", {"lng": -0.1})
        assert result.is_error
        assert "'lat' and 'lng' are required" in result.content

    @pytest.mark.asyncio
    async def test_reverse_geocode_missing_lng_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_reverse_geocode", {"lat": 51.5})
        assert result.is_error
        assert "'lat' and 'lng' are required" in result.content

    @pytest.mark.asyncio
    async def test_place_search_missing_query_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_place_search", {})
        assert result.is_error
        assert "'query' is required" in result.content

    @pytest.mark.asyncio
    async def test_directions_missing_origin_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_directions", {"destination": "B"})
        assert result.is_error
        assert "'origin' and 'destination' are required" in result.content

    @pytest.mark.asyncio
    async def test_directions_missing_destination_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_directions", {"origin": "A"})
        assert result.is_error
        assert "'origin' and 'destination' are required" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- maps_geocode shaping
# ---------------------------------------------------------------------------

class TestGeocode:
    @pytest.mark.asyncio
    async def test_geocode_shapes_result_and_attaches_key(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        payload = {
            "status": "OK",
            "results": [{
                "formatted_address": "10 Downing St, London, UK",
                "geometry": {"location": {"lat": 51.503, "lng": -0.127}},
                "place_id": "ChIJabc123",
                "types": ["premise"],
            }],
        }
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("my-key"),
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool("maps_geocode", {"address": "10 Downing St"})
        assert not result.is_error
        assert captured["method"] == "GET"
        assert "maps.googleapis.com" in captured["url"]
        assert captured["params"]["key"] == "my-key"
        assert captured["params"]["address"] == "10 Downing St"
        data = json.loads(result.content)
        assert data["status"] == "OK"
        first = data["results"][0]
        assert first["formatted_address"] == "10 Downing St, London, UK"
        assert first["lat"] == 51.503
        assert first["lng"] == -0.127
        assert first["place_id"] == "ChIJabc123"

    @pytest.mark.asyncio
    async def test_geocode_empty_results_returns_empty_list(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response={"status": "ZERO_RESULTS", "results": []}),
        )
        result = await plugin.call_tool("maps_geocode", {"address": "xyzzy"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_geocode_non_dict_response_returns_error_field(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response=[1, 2, 3]),
        )
        result = await plugin.call_tool("maps_geocode", {"address": "test"})
        assert not result.is_error
        data = json.loads(result.content)
        assert "error" in data


# ---------------------------------------------------------------------------
# Cycle 5 -- maps_reverse_geocode
# ---------------------------------------------------------------------------

class TestReverseGeocode:
    @pytest.mark.asyncio
    async def test_reverse_geocode_sends_latlng_param(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        payload = {
            "status": "OK",
            "results": [{
                "formatted_address": "Westminster, London",
                "geometry": {"location": {"lat": 51.5, "lng": -0.1}},
                "place_id": "ChIJxyz",
                "types": ["locality"],
            }],
        }
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool(
            "maps_reverse_geocode", {"lat": 51.5, "lng": -0.1}
        )
        assert not result.is_error
        assert captured["params"]["latlng"] == "51.5,-0.1"
        assert "address" not in captured["params"]
        data = json.loads(result.content)
        assert data["results"][0]["formatted_address"] == "Westminster, London"

    @pytest.mark.asyncio
    async def test_reverse_geocode_key_attached(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("secret-key"),
            fetch_fn=_make_fetch(
                response={"status": "OK", "results": []}, captured=captured
            ),
        )
        await plugin.call_tool("maps_reverse_geocode", {"lat": 0.0, "lng": 0.0})
        assert captured["params"]["key"] == "secret-key"


# ---------------------------------------------------------------------------
# Cycle 6 -- maps_place_search shaping and max_results cap
# ---------------------------------------------------------------------------

class TestPlaceSearch:
    @pytest.mark.asyncio
    async def test_place_search_shapes_results(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        payload = {
            "status": "OK",
            "results": [{
                "name": "The Coffee House",
                "formatted_address": "1 High St, London",
                "geometry": {"location": {"lat": 51.5, "lng": -0.1}},
                "place_id": "ChIJcoffee",
                "rating": 4.5,
                "types": ["cafe", "food"],
            }],
        }
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool("maps_place_search", {"query": "coffee London"})
        assert not result.is_error
        assert captured["params"]["query"] == "coffee London"
        data = json.loads(result.content)
        assert data["status"] == "OK"
        place = data["results"][0]
        assert place["name"] == "The Coffee House"
        assert place["rating"] == 4.5
        assert place["lat"] == 51.5

    @pytest.mark.asyncio
    async def test_place_search_default_max_results_is_five(self):
        from plugins.google_maps import GoogleMapsPlugin

        items = [
            {
                "name": f"Place {i}",
                "formatted_address": f"{i} St",
                "geometry": {"location": {"lat": 0, "lng": 0}},
                "place_id": f"p{i}",
                "types": [],
            }
            for i in range(8)
        ]
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response={"status": "OK", "results": items}),
        )
        result = await plugin.call_tool("maps_place_search", {"query": "test"})
        data = json.loads(result.content)
        assert len(data["results"]) == 5

    @pytest.mark.asyncio
    async def test_place_search_custom_max_results(self):
        from plugins.google_maps import GoogleMapsPlugin

        items = [
            {
                "name": f"P{i}",
                "formatted_address": "",
                "geometry": {"location": {"lat": 0, "lng": 0}},
                "place_id": f"p{i}",
                "types": [],
            }
            for i in range(6)
        ]
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response={"status": "OK", "results": items}),
        )
        result = await plugin.call_tool(
            "maps_place_search", {"query": "test", "max_results": 3}
        )
        data = json.loads(result.content)
        assert len(data["results"]) == 3


# ---------------------------------------------------------------------------
# Cycle 7 -- maps_directions shaping
# ---------------------------------------------------------------------------

class TestDirections:
    @pytest.mark.asyncio
    async def test_directions_shapes_route_and_steps(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        payload = {
            "status": "OK",
            "routes": [{
                "summary": "A1",
                "legs": [{
                    "start_address": "Origin St",
                    "end_address": "Destination Ave",
                    "distance": {"text": "5 km"},
                    "duration": {"text": "15 mins"},
                    "steps": [{
                        "html_instructions": "Head <b>north</b> on Main St",
                        "distance": {"text": "0.5 km"},
                        "duration": {"text": "2 mins"},
                        "travel_mode": "DRIVING",
                    }],
                }],
            }],
        }
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response=payload, captured=captured),
        )
        result = await plugin.call_tool(
            "maps_directions", {"origin": "Origin St", "destination": "Dest Ave"}
        )
        assert not result.is_error
        assert captured["params"]["origin"] == "Origin St"
        assert captured["params"]["destination"] == "Dest Ave"
        assert captured["params"]["mode"] == "driving"
        data = json.loads(result.content)
        assert data["status"] == "OK"
        leg = data["routes"][0]["legs"][0]
        assert leg["distance"] == "5 km"
        assert leg["duration"] == "15 mins"
        step = leg["steps"][0]
        assert "<b>" not in step["instructions"]
        assert "north" in step["instructions"]
        assert step["distance"] == "0.5 km"

    @pytest.mark.asyncio
    async def test_directions_custom_mode_sent(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(
                response={"status": "OK", "routes": []}, captured=captured
            ),
        )
        await plugin.call_tool(
            "maps_directions",
            {"origin": "A", "destination": "B", "mode": "walking"},
        )
        assert captured["params"]["mode"] == "walking"

    @pytest.mark.asyncio
    async def test_directions_empty_routes_returns_empty_list(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"),
            fetch_fn=_make_fetch(response={"status": "ZERO_RESULTS", "routes": []}),
        )
        result = await plugin.call_tool(
            "maps_directions", {"origin": "A", "destination": "B"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["routes"] == []

    @pytest.mark.asyncio
    async def test_directions_key_attached(self):
        from plugins.google_maps import GoogleMapsPlugin

        captured: dict = {}
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("dir-key"),
            fetch_fn=_make_fetch(
                response={"status": "OK", "routes": []}, captured=captured
            ),
        )
        await plugin.call_tool(
            "maps_directions", {"origin": "A", "destination": "B"}
        )
        assert captured["params"]["key"] == "dir-key"


# ---------------------------------------------------------------------------
# Cycle 8 -- transport errors + KEY SCRUB
# ---------------------------------------------------------------------------

class TestErrorsAndKeyScrub:
    @pytest.mark.asyncio
    async def test_transport_exception_is_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_error_fetch()
        )
        result = await plugin.call_tool("maps_geocode", {"address": "London"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_api_key_never_in_toolresult_content(self):
        from plugins.google_maps import GoogleMapsPlugin

        sentinel = "SENTINEL_MAPS_KEY_DO_NOT_LEAK"
        exc = RuntimeError(
            f"403 Client Error for url: "
            f"https://maps.googleapis.com/maps/api/geocode/json?address=x&key={sentinel}"
        )
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider(sentinel), fetch_fn=_error_fetch(exc)
        )
        for tool, args in (
            ("maps_geocode", {"address": "x"}),
            ("maps_reverse_geocode", {"lat": 0.0, "lng": 0.0}),
            ("maps_place_search", {"query": "x"}),
            ("maps_directions", {"origin": "A", "destination": "B"}),
        ):
            result = await plugin.call_tool(tool, args)
            assert result.is_error
            assert sentinel not in result.content
            assert "***" in result.content

    @pytest.mark.asyncio
    async def test_api_key_never_in_logs(self, caplog):
        from plugins.google_maps import GoogleMapsPlugin

        sentinel = "SENTINEL_MAPS_KEY_DO_NOT_LEAK"
        exc = RuntimeError(f"boom key={sentinel}")
        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider(sentinel), fetch_fn=_error_fetch(exc)
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("maps_geocode", {"address": "x"})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 9 -- dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleMapsPlugin(
            token_provider=_StubProvider("k"), fetch_fn=_make_fetch()
        )
        result = await plugin.call_tool("maps_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'maps_bogus'" in result.content

    @pytest.mark.asyncio
    async def test_key_is_attached_to_every_request(self):
        from plugins.google_maps import GoogleMapsPlugin

        test_cases = [
            ("maps_geocode", {"address": "x"},
             {"status": "OK", "results": []}),
            ("maps_reverse_geocode", {"lat": 0.0, "lng": 0.0},
             {"status": "OK", "results": []}),
            ("maps_place_search", {"query": "x"},
             {"status": "OK", "results": []}),
            ("maps_directions", {"origin": "A", "destination": "B"},
             {"status": "OK", "routes": []}),
        ]
        for tool, args, resp in test_cases:
            captured: dict = {}
            plugin = GoogleMapsPlugin(
                token_provider=_StubProvider("maps-key"),
                fetch_fn=_make_fetch(response=resp, captured=captured),
            )
            await plugin.call_tool(tool, args)
            assert captured["params"]["key"] == "maps-key", f"key missing for {tool}"

    def test_create_is_module_level_factory(self):
        from plugins.google_maps import create, GoogleMapsPlugin

        assert isinstance(create(), GoogleMapsPlugin)
