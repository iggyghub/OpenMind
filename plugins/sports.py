"""
Sports Scores MCP plugin — Issue #100, extended in Issue #103.

Tools: sports_scoreboard, sports_team, sports_standings.

Stateless, read-only over ESPN's public site API — no authentication, no API
key, no token storage, no SQLite:
  - Scoreboard: https://site.api.espn.com/apis/site/v2/sports/<sport>/<league>/scoreboard
  - Team:       https://site.api.espn.com/apis/site/v2/sports/<sport>/<league>/teams/<team>
  - Standings:  https://site.api.espn.com/apis/v2/sports/<sport>/<league>/standings
    (note: a different base path from scoreboard/team — /apis/v2/sports,
    not /apis/site/v2/sports)

Clones the plugins/wikipedia.py / plugins/reddit.py posture (public JSON API,
injectable async fetch_fn, JSON response shaping). Unlike Reddit, ESPN's
site.api.espn.com serves JSON to default agents (verified: HTTP 200 with a
default user agent) — so _default_fetch is a byte-clone of
wikipedia.py:_default_fetch with no User-Agent injection.

The default fetch_fn tries aiohttp then httpx — same lazy-import pattern as
plugins/wikipedia.py / plugins/reddit.py / plugins/n8n.py. Tests inject a stub.
"""
import json
import logging
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "sports"

# ADR-0005 — sports_scoreboard / sports_team fetch game and team data from
# site.api.espn.com over the public internet. Identical surface to
# wikipedia.py / reddit.py / news.py: a cloud read, no secrets, no write.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
})

_BASE = "https://site.api.espn.com/apis/site/v2/sports"
# Standings live under a different base path (/apis/v2/sports, not
# /apis/site/v2/sports) — verified live, HTTP 200 to a default agent.
_STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports"
_DEFAULT_LIMIT = 25

FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp → httpx fallback. Returns parsed JSON."""
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


def _shape_games(payload: Any) -> list[dict]:
    """Extract shaped games from an ESPN scoreboard response.

    Scoreboard responses are {"events": [{...}]} where each event carries a
    competitions[0].competitors[] list.
    """
    if not isinstance(payload, dict):
        return []
    events = payload.get("events", []) or []
    games: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions", []) or []
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors", []) or []
        shaped_competitors = []
        for c in competitors:
            if not isinstance(c, dict):
                continue
            shaped_competitors.append({
                "team": c.get("team", {}).get("displayName", ""),
                "score": c.get("score", ""),
                "homeAway": c.get("homeAway", ""),
            })
        games.append({
            "name": event.get("name", ""),
            "shortName": event.get("shortName", ""),
            "date": event.get("date", ""),
            "status": event.get("status", {}).get("type", {}).get("shortDetail", ""),
            "competitors": shaped_competitors,
        })
    return games


def _shape_team(payload: Any) -> dict:
    """Extract a shaped team summary from an ESPN team response.

    Team responses are {"team": {...}} with a record.items[] list.
    """
    if not isinstance(payload, dict):
        return {}
    team = payload.get("team", {}) or {}
    record_items = team.get("record", {}).get("items", []) or []
    record = record_items[0].get("summary", "") if record_items else ""
    return {
        "displayName": team.get("displayName", ""),
        "abbreviation": team.get("abbreviation", ""),
        "record": record,
        "standingSummary": team.get("standingSummary", ""),
    }


def _shape_standings(payload: Any) -> list[dict]:
    """Extract shaped standings groups from an ESPN standings response.

    Standings responses are {"children": [{...}]} where each child is a
    conference/division carrying a standings.entries[] list. Only one level
    of children is shaped — children[].children is not recursed.
    """
    if not isinstance(payload, dict):
        return []
    children = payload.get("children", []) or []
    groups: list[dict] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        entries = child.get("standings", {}).get("entries", []) or []
        shaped_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            stats = {
                s.get("type"): s.get("displayValue", "")
                for s in (entry.get("stats", []) or [])
                if isinstance(s, dict)
            }
            team = entry.get("team", {}) or {}
            shaped_entries.append({
                "team": team.get("displayName", ""),
                "abbreviation": team.get("abbreviation", ""),
                "wins": stats.get("wins", ""),
                "losses": stats.get("losses", ""),
                "winPercent": stats.get("winpercent", ""),
                "gamesBehind": stats.get("gamesbehind", ""),
                "streak": stats.get("streak", ""),
                "playoffSeed": stats.get("playoffseed", ""),
                "summary": stats.get("total", ""),
            })
        groups.append({
            "group": child.get("name", ""),
            "abbreviation": child.get("abbreviation", ""),
            "entries": shaped_entries,
        })
    return groups


class SportsPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="sports_scoreboard",
                description=(
                    "List games and scores for a sport/league. Optionally "
                    "scope to a date."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "sport": {
                            "type": "string",
                            "description": "ESPN sport slug, e.g. football, basketball.",
                        },
                        "league": {
                            "type": "string",
                            "description": "ESPN league slug, e.g. nfl, nba.",
                        },
                        "date": {
                            "type": "string",
                            "description": (
                                "Optional date as YYYYMMDD. Absent → the "
                                "current slate."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum games to return (default {_DEFAULT_LIMIT}).",
                        },
                    },
                    "required": ["sport", "league"],
                },
            ),
            Tool(
                name="sports_team",
                description=(
                    "Fetch a team's record and standing summary by ESPN "
                    "abbreviation."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "sport": {
                            "type": "string",
                            "description": "ESPN sport slug, e.g. basketball.",
                        },
                        "league": {
                            "type": "string",
                            "description": "ESPN league slug, e.g. nba.",
                        },
                        "team": {
                            "type": "string",
                            "description": "ESPN team abbreviation/slug, e.g. lal.",
                        },
                    },
                    "required": ["sport", "league", "team"],
                },
            ),
            Tool(
                name="sports_standings",
                description=(
                    "List league standings (win/loss records, seeds) grouped "
                    "by conference/division. Optionally scope to a season."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "sport": {
                            "type": "string",
                            "description": "ESPN sport slug, e.g. basketball.",
                        },
                        "league": {
                            "type": "string",
                            "description": "ESPN league slug, e.g. nba.",
                        },
                        "season": {
                            "type": "string",
                            "description": (
                                "Optional season year, e.g. 2025. Absent → "
                                "the current season."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                "Maximum entries per group (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                    "required": ["sport", "league"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "sports_scoreboard":
            return await self._scoreboard(args)
        if tool_name == "sports_team":
            return await self._team(args)
        if tool_name == "sports_standings":
            return await self._standings(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _scoreboard(self, args: dict) -> ToolResult:
        sport = args.get("sport")
        league = args.get("league")
        if not sport or not league:
            return ToolResult(
                content="'sport' and 'league' are required for sports_scoreboard",
                is_error=True,
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params: dict = {}
        date = args.get("date")
        if date:
            params["dates"] = date

        url = f"{_BASE}/{sport}/{league}/scoreboard"
        try:
            response = await self._fetch("GET", url, params=params)
        except Exception as exc:
            logger.error("[sports] sports_scoreboard failed: %s", exc)
            return ToolResult(content=f"ESPN request failed: {exc}", is_error=True)

        if not isinstance(response, dict):
            return ToolResult(content="Unexpected ESPN response", is_error=True)
        return ToolResult(content=json.dumps({
            "games": _shape_games(response)[:max_results],
        }))

    async def _team(self, args: dict) -> ToolResult:
        sport = args.get("sport")
        league = args.get("league")
        team = args.get("team")
        if not sport or not league or not team:
            return ToolResult(
                content="'sport', 'league' and 'team' are required for sports_team",
                is_error=True,
            )

        url = f"{_BASE}/{sport}/{league}/teams/{team}"
        try:
            response = await self._fetch("GET", url)
        except Exception as exc:
            logger.error("[sports] sports_team failed: %s", exc)
            return ToolResult(content=f"ESPN request failed: {exc}", is_error=True)

        if not isinstance(response, dict):
            return ToolResult(content="Unexpected ESPN response", is_error=True)
        return ToolResult(content=json.dumps({
            "team": _shape_team(response),
        }))


    async def _standings(self, args: dict) -> ToolResult:
        sport = args.get("sport")
        league = args.get("league")
        if not sport or not league:
            return ToolResult(
                content="'sport' and 'league' are required for sports_standings",
                is_error=True,
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params: dict = {}
        season = args.get("season")
        if season:
            params["season"] = season

        url = f"{_STANDINGS_BASE}/{sport}/{league}/standings"
        try:
            response = await self._fetch("GET", url, params=params)
        except Exception as exc:
            logger.error("[sports] sports_standings failed: %s", exc)
            return ToolResult(content=f"ESPN request failed: {exc}", is_error=True)

        if not isinstance(response, dict):
            return ToolResult(content="Unexpected ESPN response", is_error=True)
        groups = _shape_standings(response)
        for group in groups:
            group["entries"] = group["entries"][:max_results]
        return ToolResult(content=json.dumps({"standings": groups}))


def create(fetch_fn: FetchFn | None = None) -> SportsPlugin:
    return SportsPlugin(fetch_fn=fetch_fn)
