"""
Gmail MCP plugin tests -- Issue #115.

Tool: gmail_search (real Gmail API, OAuth bearer from the #112 store via
#113, not the n8n bridge). All HTTP is injected via fetch_fn and the bearer
token via a stub provider, so tests never read the keyring, run OAuth, or
hit the network.

learning-#15 POSITIVE case: Gmail auth is an `Authorization: Bearer` HEADER
built by the plugin + an on-demand refresh -- the transport carries
plugin-specific value (the reddit User-Agent precedent), UNLIKE youtube's
no-header `?key=` param (NEGATIVE, byte-cloned). So Cycle 6 asserts the
Bearer header IS attached AND the token never reaches a log / ToolResult.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.gmail import (  # noqa: E402
    GmailAPIError,
    GmailPlugin,
    REQUIRED_CAPABILITIES,
    create,
)

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


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
    Every call is appended to ``captured`` as a dict.
    """
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        captured.append({
            "method": method, "url": url,
            "headers": headers or {}, "params": params or {},
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


def _list_resp(*ids):
    return {"messages": [{"id": i, "threadId": f"t-{i}"} for i in ids]}


def _msg_resp(mid, *, frm="a@x.com", to="b@y.com", subj="Hi",
              date="Mon, 01 Jan 2026 00:00:00 +0000", snippet="hello"):
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "snippet": snippet,
        "payload": {"headers": [
            {"name": "From", "value": frm},
            {"name": "To", "value": to},
            {"name": "Subject", "value": subj},
            {"name": "Date", "value": date},
        ]},
    }


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_one(self):
        names = {t.name for t in create().list_tools()}
        assert names == {"gmail_search"}

    def test_create_plugin_named_gmail(self):
        assert create().name == "gmail"

    def test_required_capabilities_overdeclare_secrets_read(self):
        # secrets_read is a DELIBERATE over-declaration (youtube.py
        # posture-B): the plugin never calls keyring.* directly, so the
        # per-file AST audit does not auto-require it.
        assert REQUIRED_CAPABILITIES == frozenset(
            {"secrets_read", "external_data_read", "network_egress_cloud"}
        )

    def test_query_is_schema_required(self):
        tool = create().list_tools()[0]
        assert "query" in tool.schema.get("required", [])


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account (constructs, lazy error)
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.gmail as gmail_mod
        monkeypatch.setattr(gmail_mod, "_token_provider_factory", None)

        plugin = create()  # must not raise
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.gmail as gmail_mod
        monkeypatch.setattr(
            gmail_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.gmail as gmail_mod
        prov = _StubProvider(current_token="tok")
        gmail_mod.set_token_provider(lambda: prov)
        try:
            captured: list = []
            plugin = GmailPlugin(
                fetch_fn=_route_fetch(
                    {"/messages": _list_resp()}, captured
                )
            )
            result = await plugin.call_tool("gmail_search", {"query": "x"})
            assert not result.is_error
        finally:
            monkeypatch.setattr(
                gmail_mod, "_token_provider_factory", None
            )


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_missing_query_returns_error(self):
        plugin = GmailPlugin(token_provider=_StubProvider("t"),
                             fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("gmail_search", {})
        assert result.is_error
        assert "'query' is required" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- gmail_search shaping (list -> get metadata, header extraction)
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_search_shapes_messages(self):
        captured: list = []
        routes = {
            "/messages/m1": _msg_resp("m1", frm="alice@x.com",
                                      subj="First", snippet="snip1"),
            "/messages/m2": _msg_resp("m2", frm="bob@x.com",
                                      subj="Second", snippet="snip2"),
            "/messages": _list_resp("m1", "m2"),
        }
        plugin = GmailPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool(
            "gmail_search", {"query": "from:alice"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["results"][0] == {
            "id": "m1", "thread_id": "t-m1", "from": "alice@x.com",
            "to": "b@y.com", "subject": "First", "snippet": "snip1",
            "date": "Mon, 01 Jan 2026 00:00:00 +0000",
        }
        assert data["results"][1]["id"] == "m2"
        # list call carried the query; metadata format on the get calls.
        list_call = next(c for c in captured if c["url"].endswith("/messages"))
        assert list_call["url"] == f"{_BASE}/messages"
        assert list_call["params"]["q"] == "from:alice"
        get_call = next(c for c in captured if "/messages/m1" in c["url"])
        assert get_call["params"]["format"] == "metadata"

    @pytest.mark.asyncio
    async def test_header_lookup_is_case_insensitive(self):
        msg = {
            "id": "m1", "threadId": "t1", "snippet": "s",
            "payload": {"headers": [
                {"name": "fROm", "value": "weird@case.com"},
                {"name": "SUBJECT", "value": "Caps"},
            ]},
        }
        plugin = GmailPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/messages/m1": msg, "/messages": _list_resp("m1")}, []
            ),
        )
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        row = json.loads(result.content)["results"][0]
        assert row["from"] == "weird@case.com"
        assert row["subject"] == "Caps"
        assert row["to"] == ""  # absent header -> empty string

    @pytest.mark.asyncio
    async def test_default_and_custom_max_results(self):
        captured: list = []
        plugin = GmailPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/messages": _list_resp()}, captured),
        )
        await plugin.call_tool("gmail_search", {"query": "x"})
        assert captured[0]["params"]["maxResults"] == 10
        captured.clear()
        await plugin.call_tool(
            "gmail_search", {"query": "x", "max_results": 3}
        )
        assert captured[0]["params"]["maxResults"] == 3

    @pytest.mark.asyncio
    async def test_results_capped_to_max(self):
        ids = [f"m{i}" for i in range(5)]
        routes = {f"/messages/{i}": _msg_resp(i) for i in ids}
        routes["/messages"] = _list_resp(*ids)
        plugin = GmailPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, []),
        )
        result = await plugin.call_tool(
            "gmail_search", {"query": "x", "max_results": 2}
        )
        assert len(json.loads(result.content)["results"]) == 2

    @pytest.mark.asyncio
    async def test_empty_listing_returns_empty_results(self):
        plugin = GmailPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/messages": {"messages": []}}, []),
        )
        result = await plugin.call_tool("gmail_search", {"query": "none"})
        assert not result.is_error
        assert json.loads(result.content) == {"results": []}

    @pytest.mark.asyncio
    async def test_non_dict_list_response_is_error(self):
        plugin = GmailPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/messages": [1, 2]}, []),
        )
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 -- 401 -> refresh -> retry once -> then error (no infinite loop)
# ---------------------------------------------------------------------------

class TestRefreshRetry:
    @pytest.mark.asyncio
    async def test_401_triggers_one_refresh_then_succeeds(self):
        state = {"first": True}

        def list_route():
            if state["first"]:
                state["first"] = False
                raise GmailAPIError("401 Unauthorized", status=401)
            return _list_resp("m1")

        routes = {"/messages/m1": _msg_resp("m1"), "/messages": list_route}
        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = GmailPlugin(
            token_provider=prov, fetch_fn=_route_fetch(routes, captured)
        )
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert not result.is_error
        assert prov.refresh_calls == 1
        # retry used the refreshed bearer
        retry = [c for c in captured if c["url"].endswith("/messages")][-1]
        assert retry["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = GmailPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/messages": GmailAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert result.is_error
        assert prov.refresh_calls == 1  # exactly one refresh, no loop

    @pytest.mark.asyncio
    async def test_non_401_error_does_not_refresh(self):
        prov = _StubProvider(current_token="tok")
        plugin = GmailPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/messages": GmailAPIError("500", status=500)}, []
            ),
        )
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert result.is_error
        assert prov.refresh_calls == 0

    @pytest.mark.asyncio
    async def test_no_stored_token_refreshes_before_first_call(self):
        prov = _StubProvider(current_token=None,
                             refresh_tokens=("first",))
        captured: list = []
        plugin = GmailPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/messages": _list_resp()}, captured),
        )
        await plugin.call_tool("gmail_search", {"query": "x"})
        assert prov.refresh_calls == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer first"


# ---------------------------------------------------------------------------
# Cycle 6 -- transport + BEARER HEADER + token scrub (learning-#15 POSITIVE)
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_every_call(self):
        captured: list = []
        routes = {
            "/messages/m1": _msg_resp("m1"),
            "/messages": _list_resp("m1"),
        }
        plugin = GmailPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(routes, captured),
        )
        await plugin.call_tool("gmail_search", {"query": "x"})
        assert len(captured) == 2  # list + one get
        for call in captured:
            assert call["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = GmailAPIError(
            "401 Client Error for url: "
            f"{_BASE}/messages (Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = GmailPlugin(
            token_provider=_StubProvider(sentinel,
                                         refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({"/messages": exc}, []),
        )
        result = await plugin.call_tool("gmail_search", {"query": "x"})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs(self, caplog):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = GmailAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = GmailPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/messages": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("gmail_search", {"query": "x"})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 7 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = GmailPlugin(token_provider=_StubProvider("t"),
                             fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("gmail_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'gmail_bogus'" in result.content

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), GmailPlugin)
