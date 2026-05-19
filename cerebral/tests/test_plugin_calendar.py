"""
Google Calendar MCP plugin tests -- Issue #117.

Tools: calendar_list_events (read, events.list) + calendar_create_event
(write, events.insert -- real Google Calendar API, OAuth bearer from the
#112 store via #113, not the n8n bridge). All HTTP is injected via fetch_fn
and the bearer token via a stub provider, so tests never read the keyring,
run OAuth, or hit the network.

learning-#15 POSITIVE case (clone of gmail.py): Calendar auth is an
`Authorization: Bearer` HEADER built by the plugin + an on-demand refresh
-- the transport carries plugin-specific value (the reddit User-Agent /
gmail precedent), UNLIKE youtube's no-header `?key=` param (NEGATIVE,
byte-cloned). Cycle 6 asserts the Bearer header IS attached AND the token
never reaches a log / ToolResult.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.calendar import (  # noqa: E402
    CalendarAPIError,
    CalendarPlugin,
    REQUIRED_CAPABILITIES,
    create,
)

_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubProvider:
    """Bearer-token provider double. ``current_token`` is the stored token
    (None to force a refresh on first use); ``refresh()`` hands out
    ``refresh_tokens`` in order and counts calls."""

    def __init__(self, current_token=None, refresh_tokens=("refreshed",)):
        self._current = current_token
        self._refresh_tokens = list(refresh_tokens)
        self.refresh_calls = 0

    def current(self):
        return self._current

    def refresh(self):
        self.refresh_calls += 1
        tok = self._refresh_tokens[
            min(self.refresh_calls - 1, len(self._refresh_tokens) - 1)
        ]
        self._current = tok
        return tok


def _route_fetch(routes, captured):
    """Build a fetch_fn that dispatches on a substring of the URL.

    ``routes`` maps a URL substring -> response | Exception | callable().
    Every call is appended to ``captured`` as a dict including the json
    body (POSTs).
    """
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        captured.append({
            "method": method, "url": url,
            "headers": headers or {}, "params": params or {},
            "json": json or {},
        })
        for needle, resp in routes.items():
            if needle in url:
                if callable(resp) and not isinstance(resp, BaseException):
                    resp = resp()
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        raise AssertionError(f"no route for {url}")
    return fake_fetch


def _evt(eid, *, summary="Meeting", start="2026-06-01T10:00:00Z",
         end="2026-06-01T11:00:00Z", location="HQ",
         attendees=("alice@x.com",)):
    return {
        "id": eid,
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "location": location,
        "attendees": [{"email": e} for e in attendees],
    }


def _list_resp(*events):
    return {"items": list(events)}


_CREATE_OK = {
    "id": "evt-1",
    "htmlLink": "https://calendar.google.com/event?eid=evt-1",
}


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_list_and_create(self):
        names = {t.name for t in create().list_tools()}
        assert names == {"calendar_list_events", "calendar_create_event"}

    def test_create_plugin_named_calendar(self):
        assert create().name == "calendar"

    def test_required_capabilities(self):
        # secrets_read is a DELIBERATE over-declaration (gmail.py /
        # youtube.py posture-B); external_data_write is the correct
        # *required* ask-class semantic class for calendar_create_event.
        # Both external_data_* are hand-declared -- the per-file AST audit
        # maps neither.
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_create_args_schema_required(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "calendar_create_event"
        )
        assert tool.schema.get("required", []) == ["title", "start"]
        for opt in ("end", "attendees", "description"):
            assert opt in tool.schema["properties"]

    def test_list_has_no_required_args(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "calendar_list_events"
        )
        assert tool.schema.get("required", []) == []


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account (constructs, lazy error)
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.calendar as cal_mod
        monkeypatch.setattr(cal_mod, "_token_provider_factory", None)

        plugin = create()  # must not raise
        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.calendar as cal_mod
        monkeypatch.setattr(
            cal_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_no_account_lazy_error_on_create_too(self, monkeypatch):
        import plugins.calendar as cal_mod
        monkeypatch.setattr(
            cal_mod, "_token_provider_factory", lambda: None
        )
        plugin = create()
        result = await plugin.call_tool("calendar_create_event", {
            "title": "X", "start": "2026-06-01T10:00:00Z",
        })
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.calendar as cal_mod
        prov = _StubProvider(current_token="tok")
        cal_mod.set_token_provider(lambda: prov)
        try:
            captured: list = []
            plugin = CalendarPlugin(
                fetch_fn=_route_fetch(
                    {"/events": _list_resp()}, captured
                )
            )
            result = await plugin.call_tool("calendar_list_events", {})
            assert not result.is_error
        finally:
            monkeypatch.setattr(
                cal_mod, "_token_provider_factory", None
            )


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation (calendar_create_event)
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("omit", ["title", "start"])
    async def test_missing_required_arg_is_error(self, omit):
        args = {"title": "Hi", "start": "2026-06-01T10:00:00Z"}
        del args[omit]
        plugin = CalendarPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("calendar_create_event", args)
        assert result.is_error
        assert omit in result.content

    @pytest.mark.asyncio
    async def test_blank_string_arg_is_error(self):
        plugin = CalendarPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "", "start": "2026-06-01T10:00:00Z",
        })
        assert result.is_error
        assert "title" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- calendar_list_events shaping + params (timeMin/timeMax, cap)
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_list_shapes_events(self):
        captured: list = []
        routes = {
            "/events": _list_resp(
                _evt("e1", summary="First"),
                _evt("e2", summary="Second",
                     attendees=("bob@x.com", "carol@x.com")),
            ),
        }
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["events"][0] == {
            "id": "e1", "summary": "First",
            "start": "2026-06-01T10:00:00Z",
            "end": "2026-06-01T11:00:00Z",
            "location": "HQ",
            "attendees": ["alice@x.com"],
        }
        assert data["events"][1]["attendees"] == [
            "bob@x.com", "carol@x.com"
        ]
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/events"
        # default params: maxResults + singleEvents + orderBy
        assert call["params"]["maxResults"] == 10
        assert call["params"]["singleEvents"] == "true"
        assert call["params"]["orderBy"] == "startTime"

    @pytest.mark.asyncio
    async def test_time_window_params(self):
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _list_resp()}, captured),
        )
        await plugin.call_tool("calendar_list_events", {
            "from": "2026-06-01T00:00:00Z",
            "to": "2026-06-30T23:59:59Z",
        })
        assert captured[0]["params"]["timeMin"] == "2026-06-01T00:00:00Z"
        assert captured[0]["params"]["timeMax"] == "2026-06-30T23:59:59Z"

    @pytest.mark.asyncio
    async def test_default_and_custom_max_results(self):
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _list_resp()}, captured),
        )
        await plugin.call_tool("calendar_list_events", {})
        assert captured[0]["params"]["maxResults"] == 10
        captured.clear()
        await plugin.call_tool("calendar_list_events", {"max_results": 3})
        assert captured[0]["params"]["maxResults"] == 3

    @pytest.mark.asyncio
    async def test_results_capped_to_max(self):
        events = [_evt(f"e{i}") for i in range(5)]
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _list_resp(*events)}, []),
        )
        result = await plugin.call_tool(
            "calendar_list_events", {"max_results": 2}
        )
        assert len(json.loads(result.content)["events"]) == 2

    @pytest.mark.asyncio
    async def test_all_day_event_uses_date_field(self):
        evt = {
            "id": "e-allday",
            "summary": "Birthday",
            "start": {"date": "2026-06-01"},
            "end": {"date": "2026-06-02"},
        }
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _list_resp(evt)}, []),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        row = json.loads(result.content)["events"][0]
        assert row["start"] == "2026-06-01"
        assert row["end"] == "2026-06-02"
        assert row["location"] == ""
        assert row["attendees"] == []

    @pytest.mark.asyncio
    async def test_empty_listing_returns_empty_events(self):
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": {"items": []}}, []),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert not result.is_error
        assert json.loads(result.content) == {"events": []}

    @pytest.mark.asyncio
    async def test_non_dict_list_response_is_error(self):
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": [1, 2]}, []),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 -- calendar_create_event: events.insert POST body + response shape
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_builds_body_and_posts(self):
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _CREATE_OK}, captured),
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "Sync", "start": "2026-06-01T10:00:00Z",
            "end": "2026-06-01T11:00:00Z",
            "description": "weekly sync",
            "attendees": ["alice@x.com", "bob@x.com"],
        })
        assert not result.is_error
        assert json.loads(result.content) == {
            "id": "evt-1",
            "html_link": "https://calendar.google.com/event?eid=evt-1",
            "status": "created",
        }
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/events"
        assert call["headers"]["Authorization"] == "Bearer tok"
        body = call["json"]
        assert body["summary"] == "Sync"
        assert body["start"] == {"dateTime": "2026-06-01T10:00:00Z"}
        assert body["end"] == {"dateTime": "2026-06-01T11:00:00Z"}
        assert body["description"] == "weekly sync"
        assert body["attendees"] == [
            {"email": "alice@x.com"}, {"email": "bob@x.com"}
        ]

    @pytest.mark.asyncio
    async def test_end_defaults_to_start_when_omitted(self):
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _CREATE_OK}, captured),
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "Quick", "start": "2026-06-01T10:00:00Z",
        })
        assert not result.is_error
        body = captured[0]["json"]
        assert body["end"] == {"dateTime": "2026-06-01T10:00:00Z"}
        assert "description" not in body
        assert "attendees" not in body

    @pytest.mark.asyncio
    async def test_blank_attendees_filtered(self):
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": _CREATE_OK}, captured),
        )
        await plugin.call_tool("calendar_create_event", {
            "title": "Sync", "start": "2026-06-01T10:00:00Z",
            "attendees": ["a@x.com", "", "b@x.com"],
        })
        body = captured[0]["json"]
        assert body["attendees"] == [
            {"email": "a@x.com"}, {"email": "b@x.com"}
        ]

    @pytest.mark.asyncio
    async def test_non_dict_create_response_is_error(self):
        plugin = CalendarPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/events": [1, 2]}, []),
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "X", "start": "2026-06-01T10:00:00Z",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 -- 401 -> refresh -> retry once -> then error (on BOTH list+create)
# ---------------------------------------------------------------------------

class TestRefreshRetry:
    @pytest.mark.asyncio
    async def test_list_401_triggers_one_refresh_then_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise CalendarAPIError("401 Unauthorized", status=401)
            return _list_resp(_evt("e1"))

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/events": route}, captured),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert not result.is_error
        assert prov.refresh_calls == 1
        retry = [c for c in captured if "/events" in c["url"]][-1]
        assert retry["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_list_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = CalendarPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/events": CalendarAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_list_non_401_does_not_refresh(self):
        prov = _StubProvider(current_token="tok")
        plugin = CalendarPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/events": CalendarAPIError("500", status=500)}, []
            ),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error
        assert prov.refresh_calls == 0

    @pytest.mark.asyncio
    async def test_create_401_triggers_one_refresh_then_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise CalendarAPIError("401 Unauthorized", status=401)
            return _CREATE_OK

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/events": route}, captured),
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "X", "start": "2026-06-01T10:00:00Z",
        })
        assert not result.is_error
        assert prov.refresh_calls == 1
        assert captured[-1]["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_create_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = CalendarPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/events": CalendarAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "X", "start": "2026-06-01T10:00:00Z",
        })
        assert result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_no_stored_token_refreshes_before_first_call(self):
        prov = _StubProvider(current_token=None,
                             refresh_tokens=("first",))
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/events": _list_resp()}, captured),
        )
        await plugin.call_tool("calendar_list_events", {})
        assert prov.refresh_calls == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer first"


# ---------------------------------------------------------------------------
# Cycle 7 -- BEARER HEADER + token scrub (learning-#15 POSITIVE)
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_every_call(self):
        captured: list = []
        plugin = CalendarPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {"/events": _list_resp(_evt("e1"))}, captured
            ),
        )
        await plugin.call_tool("calendar_list_events", {})
        assert len(captured) == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = CalendarAPIError(
            "401 Client Error for url: "
            f"{_BASE}/events (Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = CalendarPlugin(
            token_provider=_StubProvider(sentinel,
                                         refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({"/events": exc}, []),
        )
        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs(self, caplog):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = CalendarAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = CalendarPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/events": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("calendar_list_events", {})
        assert sentinel not in caplog.text
        assert "***" in caplog.text

    @pytest.mark.asyncio
    async def test_create_token_never_in_toolresult_or_logs(self, caplog):
        sentinel = "SENTINEL_CREATE_TOKEN"
        exc = CalendarAPIError(
            f"401 for {_BASE}/events "
            f"(Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = CalendarPlugin(
            token_provider=_StubProvider(sentinel,
                                         refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({"/events": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            result = await plugin.call_tool("calendar_create_event", {
                "title": "X", "start": "2026-06-01T10:00:00Z",
            })
        assert result.is_error
        assert sentinel not in result.content
        assert sentinel not in caplog.text
        assert "***" in result.content


# ---------------------------------------------------------------------------
# Cycle 8 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = CalendarPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("calendar_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'calendar_bogus'" in result.content

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), CalendarPlugin)
