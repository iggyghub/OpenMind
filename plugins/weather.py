"""
Weather MCP plugin — Issue #25.

Tools: weather_current, weather_forecast.

Uses Open-Meteo (https://open-meteo.com) — fully open, no API key:
  - Geocoding: https://geocoding-api.open-meteo.com/v1/search
  - Forecast:  https://api.open-meteo.com/v1/forecast

Both tools take a free-form `location` (e.g. "London", "Tokyo, Japan") which
is geocoded to lat/lon before the forecast call.

The default fetch_fn tries aiohttp then httpx — same pattern as
plugins/wikipedia.py. Tests inject a stub.
"""
import json
import logging
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "weather"
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_DEFAULT_DAYS = 7

FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, params=params, json=json) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class WeatherPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    def list_tools(self) -> list[Tool]:
        location_prop = {
            "type": "string",
            "description": "Free-form location (e.g. 'London', 'Tokyo, Japan').",
        }
        return [
            Tool(
                name="weather_current",
                description="Get current weather conditions for a location via Open-Meteo.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"location": location_prop},
                    "required": ["location"],
                },
            ),
            Tool(
                name="weather_forecast",
                description=(
                    "Get a multi-day forecast (default 7 days) for a location "
                    "via Open-Meteo."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "location": location_prop,
                        "days": {
                            "type": "integer",
                            "description": f"Number of forecast days (default {_DEFAULT_DAYS}, max 16).",
                        },
                    },
                    "required": ["location"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "weather_current":
            return await self._current(args)
        if tool_name == "weather_forecast":
            return await self._forecast(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _geocode(self, location: str) -> dict | None:
        params = {"name": location, "count": 1, "format": "json"}
        response = await self._fetch("GET", _GEOCODE_URL, params=params)
        if not isinstance(response, dict):
            return None
        results = response.get("results") or []
        return results[0] if results else None

    async def _current(self, args: dict) -> ToolResult:
        location = args.get("location")
        if not location:
            return ToolResult(content="'location' is required for weather_current", is_error=True)
        try:
            place = await self._geocode(location)
        except Exception as exc:
            return ToolResult(content=f"Geocoding failed: {exc}", is_error=True)
        if not place:
            return ToolResult(content=f"Location not found: '{location}'", is_error=True)

        params = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,wind_speed_10m,weather_code,relative_humidity_2m",
            "timezone": place.get("timezone", "auto"),
        }
        try:
            response = await self._fetch("GET", _FORECAST_URL, params=params)
        except Exception as exc:
            return ToolResult(content=f"Open-Meteo fetch failed: {exc}", is_error=True)

        current = (response or {}).get("current", {})
        units = (response or {}).get("current_units", {})
        return ToolResult(content=json.dumps({
            "location": {
                "name": place.get("name"),
                "country": place.get("country"),
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
            },
            "time": current.get("time"),
            "temperature": current.get("temperature_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "humidity": current.get("relative_humidity_2m"),
            "weather_code": current.get("weather_code"),
            "units": units,
        }))

    async def _forecast(self, args: dict) -> ToolResult:
        location = args.get("location")
        if not location:
            return ToolResult(content="'location' is required for weather_forecast", is_error=True)
        days = int(args.get("days") or _DEFAULT_DAYS)
        try:
            place = await self._geocode(location)
        except Exception as exc:
            return ToolResult(content=f"Geocoding failed: {exc}", is_error=True)
        if not place:
            return ToolResult(content=f"Location not found: '{location}'", is_error=True)

        params = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum",
            "timezone": place.get("timezone", "auto"),
            "forecast_days": days,
        }
        try:
            response = await self._fetch("GET", _FORECAST_URL, params=params)
        except Exception as exc:
            return ToolResult(content=f"Open-Meteo fetch failed: {exc}", is_error=True)

        daily = (response or {}).get("daily", {})
        units = (response or {}).get("daily_units", {})
        dates = daily.get("time", []) or []
        max_t = daily.get("temperature_2m_max", []) or []
        min_t = daily.get("temperature_2m_min", []) or []
        codes = daily.get("weather_code", []) or []
        precip = daily.get("precipitation_sum", []) or []
        days_list = []
        for i, date in enumerate(dates):
            days_list.append({
                "date": date,
                "temp_max": max_t[i] if i < len(max_t) else None,
                "temp_min": min_t[i] if i < len(min_t) else None,
                "weather_code": codes[i] if i < len(codes) else None,
                "precipitation": precip[i] if i < len(precip) else None,
            })
        return ToolResult(content=json.dumps({
            "location": {
                "name": place.get("name"),
                "country": place.get("country"),
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
            },
            "days": days_list,
            "units": units,
        }))


def create(fetch_fn: FetchFn | None = None) -> WeatherPlugin:
    return WeatherPlugin(fetch_fn=fetch_fn)
