"""
Google Contacts MCP plugin tests -- Issue #229.

Tools: contacts_search, contacts_get, contacts_create, contacts_update (real
Google People API, OAuth bearer from the #112 store via #113). All HTTP is
injected via fetch_fn and the bearer token via a stub provider, so tests never
read the keyring, run OAuth, or hit the network.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.google_contacts import (  # noqa: E402
    GoogleContactsPlugin,
    REQUIRED_CAPABILITIES,
    ContactsAPIError,
    create,
)

_BASE = "https://people.googleapis.com/v1"


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


def _contact(resource_name, *, display_name="John Doe", emails=None):
    contact = {
        "resourceName": resource_name,
        "names": [{"givenName": display_name}],
    }
    if emails:
        contact["emailAddresses"] = [{"value": e} for e in emails]
    return contact


def _search_result(contact):
    return {"person": contact}


def _search_resp(*results):
    return {"results": list(results)}


_CREATE_CONTACT_OK = {
    "resourceName": "people/c1234567890",
    "names": [{"givenName": "Jane Doe"}],
}

_UPDATE_CONTACT_OK = {
    "resourceName": "people/c1234567890",
    "names": [{"givenName": "Jane Smith"}],
}


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_all_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "contacts_search",
            "contacts_get",
            "contacts_create",
            "contacts_update",
        }

    def test_create_plugin_named_google_contacts(self):
        assert create().name == "google_contacts"

    def test_required_capabilities(self):
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_contacts_search_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "contacts_search"
        )
        assert set(tool.schema.get("required", [])) == {"query"}
        assert "max_results" in tool.schema["properties"]

    def test_contacts_get_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "contacts_get"
        )
        assert tool.schema.get("required", []) == ["resource_name"]

    def test_contacts_create_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "contacts_create"
        )
        assert tool.schema.get("required", []) == ["display_name"]
        for opt in ("email", "phone"):
            assert opt in tool.schema["properties"]

    def test_contacts_update_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "contacts_update"
        )
        assert tool.schema.get("required", []) == ["resource_name"]


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.google_contacts as contacts_mod
        monkeypatch.setattr(contacts_mod, "_token_provider_factory", None)

        plugin = create()
        result = await plugin.call_tool("contacts_search", {"query": "test"})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.google_contacts as contacts_mod
        monkeypatch.setattr(
            contacts_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("contacts_search", {"query": "test"})
        assert result.is_error
        assert "no Google account connected" in result.content


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_contacts_search_missing_query_is_error(self):
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("contacts_search", {})
        assert result.is_error
        assert "query" in result.content

    @pytest.mark.asyncio
    async def test_contacts_get_missing_resource_name_is_error(self):
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("contacts_get", {})
        assert result.is_error
        assert "resource_name" in result.content

    @pytest.mark.asyncio
    async def test_contacts_create_missing_display_name_is_error(self):
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("contacts_create", {})
        assert result.is_error
        assert "display_name" in result.content

    @pytest.mark.asyncio
    async def test_contacts_update_missing_resource_name_is_error(self):
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("contacts_update", {})
        assert result.is_error
        assert "resource_name" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- contacts_search
# ---------------------------------------------------------------------------

class TestSearchContacts:
    @pytest.mark.asyncio
    async def test_search_contacts_shapes_results(self):
        captured: list = []
        routes = {
            "/people:search": _search_resp(
                _search_result(_contact(
                    "people/c1111111111", display_name="John Doe",
                    emails=["john@example.com"]
                )),
                _search_result(_contact(
                    "people/c2222222222", display_name="Jane Doe",
                    emails=["jane@example.com"]
                )),
            ),
        }
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool("contacts_search", {"query": "Doe"})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["contacts"]) == 2
        assert data["contacts"][0]["display_name"] == "John Doe"
        assert "john@example.com" in data["contacts"][0]["emails"]
        assert data["contacts"][1]["display_name"] == "Jane Doe"
        call = captured[0]
        assert call["method"] == "GET"
        assert "/people:search" in call["url"]
        assert call["params"]["query"] == "Doe"

    @pytest.mark.asyncio
    async def test_search_contacts_custom_max_results(self):
        captured: list = []
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/people:search": _search_resp()}, captured),
        )
        await plugin.call_tool("contacts_search", {
            "query": "test",
            "max_results": 5,
        })
        assert captured[0]["params"]["pageSize"] == 5

    @pytest.mark.asyncio
    async def test_search_contacts_empty(self):
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/people:search": {"results": []}}, []),
        )
        result = await plugin.call_tool("contacts_search", {"query": "test"})
        assert not result.is_error
        assert json.loads(result.content) == {"contacts": []}


# ---------------------------------------------------------------------------
# Cycle 5 -- contacts_get
# ---------------------------------------------------------------------------

class TestGetContact:
    @pytest.mark.asyncio
    async def test_get_contact_shapes_results(self):
        captured: list = []
        contact = _contact(
            "people/c1111111111", display_name="John Doe",
            emails=["john@example.com", "john.doe@work.com"]
        )
        routes = {
            "/people/c1111111111": contact,
        }
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool("contacts_get", {
            "resource_name": "people/c1111111111"
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["resource_name"] == "people/c1111111111"
        assert data["display_name"] == "John Doe"
        assert "john@example.com" in data["emails"]
        call = captured[0]
        assert call["method"] == "GET"
        assert "/people/c1111111111" in call["url"]

    @pytest.mark.asyncio
    async def test_get_contact_with_no_emails(self):
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({
                "/people/c1111111111": _contact(
                    "people/c1111111111", display_name="John Doe"
                )
            }, []),
        )
        result = await plugin.call_tool("contacts_get", {
            "resource_name": "people/c1111111111"
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["emails"] == []


# ---------------------------------------------------------------------------
# Cycle 6 -- contacts_create
# ---------------------------------------------------------------------------

class TestCreateContact:
    @pytest.mark.asyncio
    async def test_create_contact_builds_body_and_posts(self):
        captured: list = []
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({
                "/people:createContact": _CREATE_CONTACT_OK
            }, captured),
        )
        result = await plugin.call_tool("contacts_create", {
            "display_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+1234567890",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["display_name"] == "Jane Doe"
        assert data["status"] == "created"
        call = captured[0]
        assert call["method"] == "POST"
        assert "/people:createContact" in call["url"]
        assert call["json"]["names"][0]["givenName"] == "Jane Doe"
        assert call["json"]["emailAddresses"][0]["value"] == "jane@example.com"
        assert call["json"]["phoneNumbers"][0]["value"] == "+1234567890"

    @pytest.mark.asyncio
    async def test_create_contact_without_optional_fields(self):
        captured: list = []
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({
                "/people:createContact": _CREATE_CONTACT_OK
            }, captured),
        )
        result = await plugin.call_tool("contacts_create", {
            "display_name": "Simple Contact",
        })
        assert not result.is_error
        body = captured[0]["json"]
        assert body["names"][0]["givenName"] == "Simple Contact"
        assert "emailAddresses" not in body
        assert "phoneNumbers" not in body


# ---------------------------------------------------------------------------
# Cycle 7 -- contacts_update
# ---------------------------------------------------------------------------

class TestUpdateContact:
    @pytest.mark.asyncio
    async def test_update_contact_patches_fields(self):
        captured: list = []
        update_ok = {
            "resourceName": "people/c1111111111",
            "names": [{"givenName": "Jane Smith"}],
        }
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({
                "/people/c1111111111:updateContact": update_ok
            }, captured),
        )
        result = await plugin.call_tool("contacts_update", {
            "resource_name": "people/c1111111111",
            "display_name": "Jane Smith",
            "email": "jane.smith@example.com",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["resource_name"] == "people/c1111111111"
        call = captured[0]
        assert call["method"] == "PATCH"
        assert "/people/c1111111111:updateContact" in call["url"]
        assert call["json"]["names"][0]["givenName"] == "Jane Smith"
        assert call["json"]["emailAddresses"][0]["value"] == "jane.smith@example.com"

    @pytest.mark.asyncio
    async def test_update_contact_with_only_resource_name(self):
        captured: list = []
        plugin = GoogleContactsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({
                "/people/c1111111111:updateContact": _UPDATE_CONTACT_OK
            }, captured),
        )
        result = await plugin.call_tool("contacts_update", {
            "resource_name": "people/c1111111111",
        })
        assert not result.is_error
        body = captured[0]["json"]
        assert "names" in body
