"""
Weather MCP plugin tests — Issue #25.

Tools: weather_current, weather_forecast.

Uses Open-Meteo (no API key):
  - Geocoding: https://geocoding-api.open-meteo.com/v1/search
  - Forecast:  https://api.open-meteo.com/v1/forecast

The plugin geocodes the location name first, then fetches forecast data.
All HTTP calls are injected via fetch_fn; tests never hit the network.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers — fetch_fn returns different responses based on URL
# ---------------------------------------------------------------------------

def _make_routed_fetch(routes: dict, captured: list | None = None):
    """fetch_fn that returns different canned responses based on URL substring match."""
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        if captured is not None:
            captured.append({
                "method": method,
                "url": url,
                "params": params,
                "json": json,
            })
        for needle, response in routes.items():
            if needle in url:
                return response
        raise AssertionError(f"unexpected url: {url}")
    return fake_fetch


_GEOCODE_LONDON = {
    "results": [{
        "id": 1,
        "name": "London",
        "country": "United Kingdom",
        "latitude": 51.5074,
        "longitude": -0.1278,
        "timezone": "Europe/London",
    }],
}

_GEOCODE_EMPTY = {"results": []}

_CURRENT_PAYLOAD = {
    "current": {
        "time": "2026-05-04T12:00",
        "temperature_2m": 14.3,
        "wind_speed_10m": 5.6,
        "weather_code": 3,
        "relative_humidity_2m": 70,
    },
    "current_units": {
        "temperature_2m": "°C",
        "wind_speed_10m": "km/h",
    },
}

_FORECAST_PAYLOAD = {
    "daily": {
        "time": ["2026-05-04", "2026-05-05", "2026-05-06"],
        "temperature_2m_max": [15.5, 17.0, 13.0],
        "temperature_2m_min": [9.5, 10.5, 8.0],
        "weather_code": [3, 1, 61],
        "precipitation_sum": [0.0, 0.0, 4.5],
    },
    "daily_units": {
        "temperature_2m_max": "°C",
        "temperature_2m_min": "°C",
    },
}


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create()
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_two(self):
        from plugins.weather import create

        names = {t.name for t in create().list_tools()}
        assert names == {"weather_current", "weather_forecast"}

    def test_plugin_name_is_weather(self):
        from plugins.weather import create

        assert create().name == "weather"

    def test_required_args_in_schema(self):
        from plugins.weather import create

        tools = {t.name: t for t in create().list_tools()}
        assert "location" in tools["weather_current"].schema.get("required", [])
        assert "location" in tools["weather_forecast"].schema.get("required", [])


# ---------------------------------------------------------------------------
# Cycle 2 — Required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["weather_current", "weather_forecast"])
    async def test_missing_location_returns_error(self, tool):
        from plugins.weather import WeatherPlugin

        plugin = WeatherPlugin(fetch_fn=_make_routed_fetch({}))
        result = await plugin.call_tool(tool, {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — weather_current geocodes then fetches current
# ---------------------------------------------------------------------------

class TestWeatherCurrent:
    @pytest.mark.asyncio
    async def test_current_geocodes_then_fetches(self):
        from plugins.weather import WeatherPlugin

        captured: list = []
        plugin = WeatherPlugin(
            fetch_fn=_make_routed_fetch(
                {
                    "geocoding-api.open-meteo.com": _GEOCODE_LONDON,
                    "api.open-meteo.com": _CURRENT_PAYLOAD,
                },
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "weather_current", {"location": "London"}
        )
        assert not result.is_error
        # Geocode then forecast — two calls in order
        assert len(captured) == 2
        assert "geocoding-api" in captured[0]["url"]
        assert (captured[0]["params"] or {}).get("name") == "London"
        assert "api.open-meteo.com" in captured[1]["url"]
        forecast_params = captured[1]["params"] or {}
        # Coordinates from geocode reused
        assert float(forecast_params["latitude"]) == pytest.approx(51.5074)
        assert float(forecast_params["longitude"]) == pytest.approx(-0.1278)
        # Current variables requested
        assert "current" in forecast_params

        data = json.loads(result.content)
        assert data["location"]["name"] == "London"
        assert data["temperature"] == 14.3
        assert "units" in data

    @pytest.mark.asyncio
    async def test_current_unknown_location_returns_error(self):
        from plugins.weather import WeatherPlugin

        plugin = WeatherPlugin(
            fetch_fn=_make_routed_fetch(
                {"geocoding-api": _GEOCODE_EMPTY}
            )
        )
        result = await plugin.call_tool(
            "weather_current", {"location": "Atlantis"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_current_geocode_network_error_returns_error(self):
        from plugins.weather import WeatherPlugin

        async def fake_fetch(method, url, *, headers=None, params=None, json=None):
            raise ConnectionError("offline")

        plugin = WeatherPlugin(fetch_fn=fake_fetch)
        result = await plugin.call_tool(
            "weather_current", {"location": "London"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — weather_forecast geocodes + multi-day
# ---------------------------------------------------------------------------

class TestWeatherForecast:
    @pytest.mark.asyncio
    async def test_forecast_default_seven_days(self):
        from plugins.weather import WeatherPlugin

        captured: list = []
        plugin = WeatherPlugin(
            fetch_fn=_make_routed_fetch(
                {
                    "geocoding-api": _GEOCODE_LONDON,
                    "api.open-meteo.com": _FORECAST_PAYLOAD,
                },
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "weather_forecast", {"location": "London"}
        )
        assert not result.is_error
        forecast_params = captured[1]["params"] or {}
        assert int(forecast_params.get("forecast_days", 0)) == 7

        data = json.loads(result.content)
        assert "days" in data
        assert isinstance(data["days"], list)
        assert len(data["days"]) == 3  # only 3 days returned by fake
        first = data["days"][0]
        assert "date" in first
        assert first["temp_max"] == 15.5
        assert first["temp_min"] == 9.5

    @pytest.mark.asyncio
    async def test_forecast_respects_days_arg(self):
        from plugins.weather import WeatherPlugin

        captured: list = []
        plugin = WeatherPlugin(
            fetch_fn=_make_routed_fetch(
                {
                    "geocoding-api": _GEOCODE_LONDON,
                    "api.open-meteo.com": _FORECAST_PAYLOAD,
                },
                captured=captured,
            )
        )
        await plugin.call_tool(
            "weather_forecast", {"location": "London", "days": 3}
        )
        assert int((captured[1]["params"] or {}).get("forecast_days", 0)) == 3

    @pytest.mark.asyncio
    async def test_forecast_unknown_location_returns_error(self):
        from plugins.weather import WeatherPlugin

        plugin = WeatherPlugin(
            fetch_fn=_make_routed_fetch({"geocoding-api": _GEOCODE_EMPTY})
        )
        result = await plugin.call_tool(
            "weather_forecast", {"location": "Atlantis"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.weather import WeatherPlugin

        plugin = WeatherPlugin(fetch_fn=_make_routed_fetch({}))
        result = await plugin.call_tool("weather_alerts", {"location": "x"})
        assert result.is_error
