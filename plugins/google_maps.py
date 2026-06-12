"""
Google Maps MCP plugin -- Issue #226, ADR-0005.

Four tools, hitting the **real Google Maps Platform APIs** directly, authorized
by a static API key read from the active profile's keyring or the
``GOOGLE_MAPS_API_KEY`` env-var fallback (youtube.py posture, ADR-0005
2026-05-23 amendment):

  - ``maps_geocode``          -- Geocoding API (address -> lat/lng + components).
  - ``maps_reverse_geocode``  -- Geocoding API (lat/lng -> address).
  - ``maps_place_search``     -- Places API Text Search (keyword -> places list).
  - ``maps_directions``       -- Directions API (origin -> destination, steps).

API key: ``GOOGLE_MAPS_API_KEY`` (env var) / ``google_maps`` provider name
(per-profile keyring via #112 CredentialStore, field ``api_token``).

**Static-key posture (youtube.py precedent, ADR-0005 posture B):**
  - No OAuth, no token refresh.
  - Key is a ``?key=`` query parameter on every request.
  - ``secrets_read`` is deliberately over-declared (the same auditable
    intent as youtube.py -- the plugin reads credential material and the
    silent-class free pass is the wrong default).
  - TokenProvider Protocol carries only ``current()`` (static key, no refresh).
  - Module-level ``set_token_provider`` seam wired from ``cerebral/main.py``.
  - Constructor-injected provider wins over the module-level seam (tests
    pass their own stub; production leaves it None and main.py wires the
    factory at startup).

**No live-verify in this slice.** Live verification is batched into slice
V.1 (#239) so the autonomous loop is never blocked on browser consent or
key setup.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_maps"

# ADR-0005 / Issue #226.
#   - external_data_read + network_egress_cloud: maps_* fetch data from
#     maps.googleapis.com over the internet.
#   - secrets_read is a DELIBERATE over-declaration matching the youtube.py
#     posture (see module docstring). Do not remove it.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "network_egress_cloud",
})

_GEOCODE_BASE = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_BASE = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_DIRECTIONS_BASE = "https://maps.googleapis.com/maps/api/directions/json"

_FACTORY_NOT_WIRED_MSG = "Google Maps is not available -- token provider not wired"
_NO_TOKEN_MSG = "GOOGLE_MAPS_API_KEY is not configured"

FetchFn = Callable[..., Awaitable[Any]]


class TokenProvider(Protocol):
    """Per-active-profile API-key handle wired from main.py.

    Carries ONLY ``current()`` because Google Maps API keys are STATIC
    user-rotated values (Google Cloud Console -> Credentials -> API key).
    No OAuth refresh capability. Mirrors plugins/youtube.py /
    plugins/todoist.py / plugins/notion.py / plugins/toggl.py."""

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_google_maps_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin."""
    global _token_provider_factory
    _token_provider_factory = fn


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    The Maps key is a ``?key=`` query parameter carried in ``params``; no
    auth header needed. Same no-header pattern as plugins/youtube.py.
    """
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers,
                                        params=params, json=json) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers,
                                        params=params, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed -- cannot make HTTP requests")


def _shape_geocode(payload: Any) -> dict:
    """Flatten the first result from a Geocoding API response."""
    if not isinstance(payload, dict):
        return {"error": "Unexpected response format"}
    status = payload.get("status", "")
    results = payload.get("results", []) or []
    if not results:
        return {"status": status, "results": []}
    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        loc = (r.get("geometry") or {}).get("location") or {}
        out.append({
            "formatted_address": r.get("formatted_address", ""),
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "place_id": r.get("place_id", ""),
            "types": r.get("types", []),
        })
    return {"status": status, "results": out}


def _shape_places(payload: Any) -> dict:
    """Flatten a Places Text Search API response."""
    if not isinstance(payload, dict):
        return {"error": "Unexpected response format"}
    status = payload.get("status", "")
    results = payload.get("results", []) or []
    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        loc = (r.get("geometry") or {}).get("location") or {}
        out.append({
            "name": r.get("name", ""),
            "formatted_address": r.get("formatted_address", ""),
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "place_id": r.get("place_id", ""),
            "rating": r.get("rating"),
            "types": r.get("types", []),
        })
    return {"status": status, "results": out}


def _shape_directions(payload: Any) -> dict:
    """Flatten a Directions API response into a summary + step list."""
    if not isinstance(payload, dict):
        return {"error": "Unexpected response format"}
    status = payload.get("status", "")
    routes = payload.get("routes", []) or []
    if not routes:
        return {"status": status, "routes": []}
    out = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        legs = route.get("legs", []) or []
        shaped_legs = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            steps = []
            for step in (leg.get("steps") or []):
                if not isinstance(step, dict):
                    continue
                import re as _re
                instructions = step.get("html_instructions", "")
                instructions = _re.sub(r"<[^>]+>", " ", instructions).strip()
                steps.append({
                    "instructions": instructions,
                    "distance": (step.get("distance") or {}).get("text", ""),
                    "duration": (step.get("duration") or {}).get("text", ""),
                    "travel_mode": step.get("travel_mode", ""),
                })
            shaped_legs.append({
                "start_address": leg.get("start_address", ""),
                "end_address": leg.get("end_address", ""),
                "distance": (leg.get("distance") or {}).get("text", ""),
                "duration": (leg.get("duration") or {}).get("text", ""),
                "steps": steps,
            })
        out.append({
            "summary": route.get("summary", ""),
            "legs": shaped_legs,
        })
    return {"status": status, "routes": out}


class GoogleMapsPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="maps_geocode",
                description=(
                    "Geocode a street address to latitude/longitude and address "
                    "components using the Google Maps Geocoding API."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "The street address to geocode.",
                        },
                    },
                    "required": ["address"],
                },
            ),
            Tool(
                name="maps_reverse_geocode",
                description=(
                    "Reverse-geocode a latitude/longitude coordinate pair to a "
                    "human-readable address using the Google Maps Geocoding API."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "lat": {
                            "type": "number",
                            "description": "Latitude.",
                        },
                        "lng": {
                            "type": "number",
                            "description": "Longitude.",
                        },
                    },
                    "required": ["lat", "lng"],
                },
            ),
            Tool(
                name="maps_place_search",
                description=(
                    "Search for places matching a text query using the Google "
                    "Maps Places Text Search API. Returns name, address, "
                    "coordinates, place id, and rating."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text search query (e.g. 'coffee shops in London').",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of places to return (default 5).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="maps_directions",
                description=(
                    "Get turn-by-turn driving directions between two locations "
                    "using the Google Maps Directions API."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "origin": {
                            "type": "string",
                            "description": "Start address or place name.",
                        },
                        "destination": {
                            "type": "string",
                            "description": "End address or place name.",
                        },
                        "mode": {
                            "type": "string",
                            "description": (
                                "Travel mode: driving (default), walking, "
                                "bicycling, or transit."
                            ),
                        },
                    },
                    "required": ["origin", "destination"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "maps_geocode":
            return await self._geocode(args)
        if tool_name == "maps_reverse_geocode":
            return await self._reverse_geocode(args)
        if tool_name == "maps_place_search":
            return await self._place_search(args)
        if tool_name == "maps_directions":
            return await self._directions(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None."""
        if self._provider is not None:
            return self._provider, None
        factory = self._token_factory()
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        provider = factory()
        if provider is None:
            return None, _NO_TOKEN_MSG
        return provider, None

    @staticmethod
    def _token_factory() -> Optional[TokenProviderFactory]:
        return _token_provider_factory

    def _scrub(self, text: str) -> str:
        """Strip every API key seen this call from text bound for a log or
        ToolResult (the key is a ?key= param; HTTP errors embed the full URL)."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider) -> str:
        """Get the static API key from the provider. Records it for scrubbing."""
        token = provider.current() or ""
        if token:
            self._seen_tokens.add(token)
        return token

    async def _get(self, url: str, params: dict, token: str) -> Any:
        return await self._fetch("GET", url, params={**params, "key": token})

    async def _geocode(self, args: dict) -> ToolResult:
        address = args.get("address")
        if not address:
            return ToolResult(
                content="'address' is required for maps_geocode", is_error=True
            )
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        try:
            response = await self._get(_GEOCODE_BASE, {"address": address}, token)
        except Exception as exc:
            logger.error("[google_maps] maps_geocode failed: %s", self._scrub(str(exc)))
            return ToolResult(
                content=self._scrub(f"maps_geocode failed: {exc}"), is_error=True
            )
        return ToolResult(content=json.dumps(_shape_geocode(response)))

    async def _reverse_geocode(self, args: dict) -> ToolResult:
        lat = args.get("lat")
        lng = args.get("lng")
        if lat is None or lng is None:
            return ToolResult(
                content="'lat' and 'lng' are required for maps_reverse_geocode",
                is_error=True,
            )
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        latlng = f"{lat},{lng}"
        try:
            response = await self._get(_GEOCODE_BASE, {"latlng": latlng}, token)
        except Exception as exc:
            logger.error(
                "[google_maps] maps_reverse_geocode failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"maps_reverse_geocode failed: {exc}"), is_error=True
            )
        return ToolResult(content=json.dumps(_shape_geocode(response)))

    async def _place_search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(
                content="'query' is required for maps_place_search", is_error=True
            )
        max_results = int(args.get("max_results") or 5)
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        try:
            response = await self._get(_PLACES_BASE, {"query": query}, token)
        except Exception as exc:
            logger.error(
                "[google_maps] maps_place_search failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"maps_place_search failed: {exc}"), is_error=True
            )
        shaped = _shape_places(response)
        if "results" in shaped:
            shaped["results"] = shaped["results"][:max_results]
        return ToolResult(content=json.dumps(shaped))

    async def _directions(self, args: dict) -> ToolResult:
        origin = args.get("origin")
        destination = args.get("destination")
        if not origin or not destination:
            return ToolResult(
                content="'origin' and 'destination' are required for maps_directions",
                is_error=True,
            )
        mode = args.get("mode") or "driving"
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        token = self._take_token(provider)
        try:
            response = await self._get(
                _DIRECTIONS_BASE,
                {"origin": origin, "destination": destination, "mode": mode},
                token,
            )
        except Exception as exc:
            logger.error(
                "[google_maps] maps_directions failed: %s", self._scrub(str(exc))
            )
            return ToolResult(
                content=self._scrub(f"maps_directions failed: {exc}"), is_error=True
            )
        return ToolResult(content=json.dumps(_shape_directions(response)))


def create(fetch_fn: FetchFn | None = None) -> GoogleMapsPlugin:
    """Zero-arg-by-default factory the orchestrator discovers.

    ``fetch_fn`` is for tests; production leaves it None. The token
    provider is wired separately via ``set_token_provider`` from
    ``cerebral/main.py``."""
    return GoogleMapsPlugin(fetch_fn=fetch_fn)
