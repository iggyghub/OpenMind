"""
Sports Scores MCP plugin tests — Issue #100.

Tools: sports_scoreboard, sports_team.

Stateless, read-only over ESPN's public site API. All HTTP calls are injected
via fetch_fn so tests never hit the network. ESPN serves JSON to default
agents (no User-Agent gotcha, unlike Reddit) so the default transport is a
byte-clone of wikipedia.py — only its no-http-lib RuntimeError guard is
exercised here.
"""
import json
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


def _scoreboard(*events: dict) -> dict:
    """Wrap event dicts in ESPN's scoreboard envelope."""
    return {"events": list(events)}


def _event(*, name="A at B", short="A @ B", date="2026-05-18T00:00Z",
           status="Final", competitors=None) -> dict:
    return {
        "name": name,
        "shortName": short,
        "date": date,
        "status": {"type": {"shortDetail": status}},
        "competitions": [{"competitors": competitors or []}],
    }


def _competitor(team="Team", score="0", home_away="home") -> dict:
    return {
        "team": {"displayName": team},
        "score": score,
        "homeAway": home_away,
    }


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_two(self):
        from plugins.sports import create

        names = {t.name for t in create().list_tools()}
        assert names == {"sports_scoreboard", "sports_team"}

    def test_create_plugin_named_sports(self):
        from plugins.sports import create

        assert create().name == "sports"

    def test_required_capabilities_are_cloud_read(self):
        from plugins.sports import REQUIRED_CAPABILITIES

        assert REQUIRED_CAPABILITIES == frozenset(
            {"external_data_read", "network_egress_cloud"}
        )

    def test_tools_have_required_args_in_schema(self):
        from plugins.sports import create

        tools = {t.name: t for t in create().list_tools()}
        sb_req = tools["sports_scoreboard"].schema.get("required", [])
        assert "sport" in sb_req and "league" in sb_req
        tm_req = tools["sports_team"].schema.get("required", [])
        assert "sport" in tm_req and "league" in tm_req and "team" in tm_req


# ---------------------------------------------------------------------------
# Cycle 2 — Required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_scoreboard_missing_sport_returns_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("sports_scoreboard", {"league": "nba"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_scoreboard_missing_league_returns_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool(
            "sports_scoreboard", {"sport": "basketball"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_team_missing_team_returns_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool(
            "sports_team", {"sport": "basketball", "league": "nba"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — sports_scoreboard listing + shaping
# ---------------------------------------------------------------------------

class TestScoreboard:
    @pytest.mark.asyncio
    async def test_scoreboard_shapes_games(self):
        from plugins.sports import SportsPlugin

        captured: dict = {}
        plugin = SportsPlugin(
            fetch_fn=_make_fetch(
                response=_scoreboard(_event(
                    name="Cavaliers at Pistons",
                    short="CLE @ DET",
                    date="2026-05-18T00:00Z",
                    status="Final",
                    competitors=[
                        _competitor("Detroit Pistons", "94", "home"),
                        _competitor("Cleveland Cavaliers", "88", "away"),
                    ],
                )),
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "sports_scoreboard", {"sport": "basketball", "league": "nba"}
        )
        assert not result.is_error
        assert captured["method"] == "GET"
        assert captured["url"] == (
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        )
        game = json.loads(result.content)["games"][0]
        assert game["name"] == "Cavaliers at Pistons"
        assert game["shortName"] == "CLE @ DET"
        assert game["date"] == "2026-05-18T00:00Z"
        assert game["status"] == "Final"
        assert game["competitors"][0] == {
            "team": "Detroit Pistons", "score": "94", "homeAway": "home"
        }
        assert game["competitors"][1]["team"] == "Cleveland Cavaliers"

    @pytest.mark.asyncio
    async def test_scoreboard_date_sets_dates_param(self):
        from plugins.sports import SportsPlugin

        captured: dict = {}
        plugin = SportsPlugin(
            fetch_fn=_make_fetch(response=_scoreboard(), captured=captured)
        )
        await plugin.call_tool(
            "sports_scoreboard",
            {"sport": "basketball", "league": "nba", "date": "20260518"},
        )
        assert (captured["params"] or {}).get("dates") == "20260518"

    @pytest.mark.asyncio
    async def test_scoreboard_no_date_omits_dates_param(self):
        from plugins.sports import SportsPlugin

        captured: dict = {}
        plugin = SportsPlugin(
            fetch_fn=_make_fetch(response=_scoreboard(), captured=captured)
        )
        await plugin.call_tool(
            "sports_scoreboard", {"sport": "basketball", "league": "nba"}
        )
        assert "dates" not in (captured["params"] or {})

    @pytest.mark.asyncio
    async def test_scoreboard_respects_max_results(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(
            fetch_fn=_make_fetch(
                response=_scoreboard(_event(), _event(), _event())
            )
        )
        result = await plugin.call_tool(
            "sports_scoreboard",
            {"sport": "basketball", "league": "nba", "max_results": 2},
        )
        assert len(json.loads(result.content)["games"]) == 2

    @pytest.mark.asyncio
    async def test_scoreboard_empty_events_returns_empty(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch(response=_scoreboard()))
        result = await plugin.call_tool(
            "sports_scoreboard", {"sport": "basketball", "league": "nba"}
        )
        assert not result.is_error
        assert json.loads(result.content)["games"] == []

    @pytest.mark.asyncio
    async def test_scoreboard_non_dict_response_is_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch(response=["nope"]))
        result = await plugin.call_tool(
            "sports_scoreboard", {"sport": "basketball", "league": "nba"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_scoreboard_network_error_is_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool(
            "sports_scoreboard", {"sport": "basketball", "league": "nba"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — sports_team
# ---------------------------------------------------------------------------

class TestTeam:
    @pytest.mark.asyncio
    async def test_team_shapes_summary(self):
        from plugins.sports import SportsPlugin

        captured: dict = {}
        plugin = SportsPlugin(
            fetch_fn=_make_fetch(
                response={"team": {
                    "displayName": "Los Angeles Lakers",
                    "abbreviation": "LAL",
                    "standingSummary": "1st in Pacific Division",
                    "record": {"items": [{"summary": "53-29"}]},
                }},
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "sports_team",
            {"sport": "basketball", "league": "nba", "team": "lal"},
        )
        assert not result.is_error
        assert captured["url"] == (
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/lal"
        )
        team = json.loads(result.content)["team"]
        assert team["displayName"] == "Los Angeles Lakers"
        assert team["abbreviation"] == "LAL"
        assert team["record"] == "53-29"
        assert team["standingSummary"] == "1st in Pacific Division"

    @pytest.mark.asyncio
    async def test_team_missing_record_items_is_blank(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(
            fetch_fn=_make_fetch(
                response={"team": {"displayName": "X", "abbreviation": "X"}}
            )
        )
        result = await plugin.call_tool(
            "sports_team",
            {"sport": "basketball", "league": "nba", "team": "x"},
        )
        assert not result.is_error
        assert json.loads(result.content)["team"]["record"] == ""

    @pytest.mark.asyncio
    async def test_team_non_dict_response_is_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch(response="oops"))
        result = await plugin.call_tool(
            "sports_team",
            {"sport": "basketball", "league": "nba", "team": "lal"},
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_team_network_error_is_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool(
            "sports_team",
            {"sport": "basketball", "league": "nba", "team": "lal"},
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.sports import SportsPlugin

        plugin = SportsPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("sports_random", {"sport": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — default transport guard (no UA gotcha; only the no-http-lib path)
# ---------------------------------------------------------------------------

class TestDefaultTransport:
    @pytest.mark.asyncio
    async def test_default_fetch_no_http_lib_raises(self, monkeypatch):
        from plugins import sports

        monkeypatch.setitem(sys.modules, "aiohttp", None)
        monkeypatch.setitem(sys.modules, "httpx", None)
        with pytest.raises(RuntimeError):
            await sports._default_fetch(
                "GET", "https://site.api.espn.com/x"
            )
