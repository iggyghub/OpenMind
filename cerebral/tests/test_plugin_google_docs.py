"""
Google Docs MCP plugin tests -- Issue #224.

Tools: docs_list (read) + docs_read (read) + docs_create (write) +
docs_append (write). All HTTP is injected via fetch_fn and the bearer token
via a stub provider, so tests never read the keyring, run OAuth, or hit the
network.

learning-#15 POSITIVE case: the auth is an ``Authorization: Bearer`` header
built by the plugin -- identical to the gmail.py / calendar.py surface.
Cycle 6 asserts the Bearer header IS attached AND the token never reaches a
log / ToolResult.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.google_docs import (  # noqa: E402
    DocsAPIError,
    GoogleDocsPlugin,
    REQUIRED_CAPABILITIES,
    create,
)

_DOCS_BASE = "https://docs.googleapis.com/v1/documents"
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"


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
    Every call is appended to ``captured`` as a dict (including json body).
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


def _file_resp(fid, name="My Doc", modified="2026-01-01T00:00:00Z",
               link="https://docs.google.com/d/abc"):
    return {"id": fid, "name": name, "modifiedTime": modified,
            "webViewLink": link}


def _list_resp(*files):
    return {"files": list(files)}


def _doc_resp(doc_id, title="My Doc", body_text="Hello\n"):
    return {
        "documentId": doc_id,
        "title": title,
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": body_text}}
                        ]
                    }
                }
            ]
        },
    }


def _create_resp(doc_id, title="My Doc", revision="rev1"):
    return {"documentId": doc_id, "title": title, "revisionId": revision}


def _batch_update_resp(doc_id):
    return {
        "documentId": doc_id,
        "writeControl": {"requiredRevisionId": "rev2"},
    }


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_all_four(self):
        names = {t.name for t in create().list_tools()}
        assert names == {"docs_list", "docs_read", "docs_create", "docs_append"}

    def test_create_plugin_named_google_docs(self):
        assert create().name == "google_docs"

    def test_required_capabilities(self):
        # secrets_read is a DELIBERATE over-declaration (posture-B);
        # external_data_write is the correct *required* ask-class for
        # docs_create / docs_append. Both are hand-declared.
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_docs_create_is_irreversible(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_create")
        assert tool.irreversible is True

    def test_docs_append_is_irreversible(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_append")
        assert tool.irreversible is True

    def test_docs_list_is_not_irreversible(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_list")
        assert tool.irreversible is False

    def test_docs_read_is_not_irreversible(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_read")
        assert tool.irreversible is False

    def test_docs_read_schema_required(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_read")
        assert "document_id" in tool.schema.get("required", [])

    def test_docs_create_schema_required(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_create")
        assert "title" in tool.schema.get("required", [])

    def test_docs_append_schema_required(self):
        tool = next(t for t in create().list_tools() if t.name == "docs_append")
        assert set(tool.schema.get("required", [])) == {"document_id", "text"}


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account (constructs, lazy error)
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.google_docs as docs_mod
        monkeypatch.setattr(docs_mod, "_token_provider_factory", None)

        plugin = create()  # must not raise
        result = await plugin.call_tool("docs_list", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.google_docs as docs_mod
        monkeypatch.setattr(
            docs_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("docs_list", {})
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.google_docs as docs_mod
        prov = _StubProvider(current_token="tok")
        docs_mod.set_token_provider(lambda: prov)
        try:
            captured: list = []
            plugin = GoogleDocsPlugin(
                fetch_fn=_route_fetch(
                    {_DRIVE_FILES: _list_resp()}, captured
                )
            )
            result = await plugin.call_tool("docs_list", {})
            assert not result.is_error
        finally:
            monkeypatch.setattr(
                docs_mod, "_token_provider_factory", None
            )


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_missing_document_id_for_read(self):
        plugin = GoogleDocsPlugin(token_provider=_StubProvider("t"),
                                  fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("docs_read", {})
        assert result.is_error
        assert "'document_id' is required" in result.content

    @pytest.mark.asyncio
    async def test_missing_title_for_create(self):
        plugin = GoogleDocsPlugin(token_provider=_StubProvider("t"),
                                  fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("docs_create", {})
        assert result.is_error
        assert "title" in result.content

    @pytest.mark.asyncio
    @pytest.mark.parametrize("omit", ["document_id", "text"])
    async def test_missing_append_arg(self, omit):
        args = {"document_id": "d1", "text": "hello"}
        del args[omit]
        plugin = GoogleDocsPlugin(token_provider=_StubProvider("t"),
                                  fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("docs_append", args)
        assert result.is_error
        assert omit in result.content

    @pytest.mark.asyncio
    async def test_blank_document_id_is_error(self):
        plugin = GoogleDocsPlugin(token_provider=_StubProvider("t"),
                                  fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("docs_read", {"document_id": ""})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 -- response shaping
# ---------------------------------------------------------------------------

class TestShaping:
    @pytest.mark.asyncio
    async def test_docs_list_shapes_files(self):
        f1 = _file_resp("d1", name="Doc One", modified="2026-01-02T00:00:00Z",
                        link="https://docs.google.com/d/d1")
        f2 = _file_resp("d2", name="Doc Two", modified="2026-01-01T00:00:00Z",
                        link="https://docs.google.com/d/d2")
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp(f1, f2)}, []),
        )
        result = await plugin.call_tool("docs_list", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["documents"][0] == {
            "id": "d1", "name": "Doc One",
            "modified_time": "2026-01-02T00:00:00Z",
            "web_view_link": "https://docs.google.com/d/d1",
        }
        assert data["documents"][1]["id"] == "d2"

    @pytest.mark.asyncio
    async def test_docs_list_default_max_results(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("docs_list", {})
        assert captured[0]["params"]["pageSize"] == 10

    @pytest.mark.asyncio
    async def test_docs_list_custom_max_results(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("docs_list", {"max_results": 3})
        assert captured[0]["params"]["pageSize"] == 3

    @pytest.mark.asyncio
    async def test_docs_list_filters_to_docs_mime(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("docs_list", {})
        q = captured[0]["params"]["q"]
        assert "application/vnd.google-apps.document" in q

    @pytest.mark.asyncio
    async def test_docs_read_extracts_body_text(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/documents/doc1": _doc_resp("doc1", title="Hello Doc",
                                              body_text="Hello World\n")},
                [],
            ),
        )
        result = await plugin.call_tool("docs_read", {"document_id": "doc1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["document_id"] == "doc1"
        assert data["title"] == "Hello Doc"
        assert data["body"] == "Hello World\n"

    @pytest.mark.asyncio
    async def test_docs_read_empty_body(self):
        resp = {"documentId": "d1", "title": "Empty", "body": {}}
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/documents/d1": resp}, []),
        )
        result = await plugin.call_tool("docs_read", {"document_id": "d1"})
        assert not result.is_error
        assert json.loads(result.content)["body"] == ""

    @pytest.mark.asyncio
    async def test_docs_create_shapes_response(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {_DOCS_BASE: _create_resp("newdoc", title="New Doc",
                                          revision="rev1")},
                [],
            ),
        )
        result = await plugin.call_tool("docs_create", {"title": "New Doc"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data == {
            "document_id": "newdoc",
            "title": "New Doc",
            "revision_id": "rev1",
        }

    @pytest.mark.asyncio
    async def test_docs_append_shapes_response(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {":batchUpdate": _batch_update_resp("d1")}, []
            ),
        )
        result = await plugin.call_tool("docs_append", {
            "document_id": "d1", "text": "More text",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data == {
            "document_id": "d1",
            "revision_id": "rev2",
            "status": "appended",
        }

    @pytest.mark.asyncio
    async def test_docs_append_posts_correct_batchupdate_body(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {":batchUpdate": _batch_update_resp("d1")}, captured
            ),
        )
        await plugin.call_tool("docs_append", {
            "document_id": "d1", "text": "Appended text",
        })
        call = next(c for c in captured if ":batchUpdate" in c["url"])
        assert call["method"] == "POST"
        requests = call["json"]["requests"]
        assert len(requests) == 1
        insert = requests[0]["insertText"]
        assert insert["text"] == "Appended text"
        assert "endOfSegmentLocation" in insert

    @pytest.mark.asyncio
    async def test_docs_list_empty_returns_empty(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, []),
        )
        result = await plugin.call_tool("docs_list", {})
        assert not result.is_error
        assert json.loads(result.content) == {"documents": []}


# ---------------------------------------------------------------------------
# Cycle 5 -- 401 -> refresh -> retry once -> then error (no infinite loop)
# ---------------------------------------------------------------------------

class TestRefreshRetry:
    @pytest.mark.asyncio
    async def test_list_401_triggers_one_refresh_then_succeeds(self):
        state = {"first": True}

        def list_route():
            if state["first"]:
                state["first"] = False
                raise DocsAPIError("401 Unauthorized", status=401)
            return _list_resp(_file_resp("d1"))

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({_DRIVE_FILES: list_route}, captured),
        )
        result = await plugin.call_tool("docs_list", {})
        assert not result.is_error
        assert prov.refresh_calls == 1
        retry = [c for c in captured if _DRIVE_FILES in c["url"]][-1]
        assert retry["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {_DRIVE_FILES: DocsAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("docs_list", {})
        assert result.is_error
        assert prov.refresh_calls == 1  # exactly one refresh, no loop

    @pytest.mark.asyncio
    async def test_non_401_does_not_refresh(self):
        prov = _StubProvider(current_token="tok")
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {_DRIVE_FILES: DocsAPIError("500", status=500)}, []
            ),
        )
        result = await plugin.call_tool("docs_list", {})
        assert result.is_error
        assert prov.refresh_calls == 0

    @pytest.mark.asyncio
    async def test_no_stored_token_refreshes_before_first_call(self):
        prov = _StubProvider(current_token=None,
                             refresh_tokens=("first",))
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("docs_list", {})
        assert prov.refresh_calls == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer first"

    @pytest.mark.asyncio
    async def test_read_401_triggers_refresh(self):
        state = {"first": True}

        def read_route():
            if state["first"]:
                state["first"] = False
                raise DocsAPIError("401", status=401)
            return _doc_resp("d1")

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/documents/d1": read_route}, []),
        )
        result = await plugin.call_tool("docs_read", {"document_id": "d1"})
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_create_401_triggers_refresh(self):
        state = {"first": True}

        def create_route():
            if state["first"]:
                state["first"] = False
                raise DocsAPIError("401", status=401)
            return _create_resp("new1")

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({_DOCS_BASE: create_route}, []),
        )
        result = await plugin.call_tool("docs_create", {"title": "T"})
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_append_401_triggers_refresh(self):
        state = {"first": True}

        def append_route():
            if state["first"]:
                state["first"] = False
                raise DocsAPIError("401", status=401)
            return _batch_update_resp("d1")

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = GoogleDocsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({":batchUpdate": append_route}, []),
        )
        result = await plugin.call_tool("docs_append", {
            "document_id": "d1", "text": "x",
        })
        assert not result.is_error
        assert prov.refresh_calls == 1


# ---------------------------------------------------------------------------
# Cycle 6 -- Bearer header + token scrub (learning-#15 POSITIVE)
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_list(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("docs_list", {})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_read(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {"/documents/d1": _doc_resp("d1")}, captured
            ),
        )
        await plugin.call_tool("docs_read", {"document_id": "d1"})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_create(self):
        captured: list = []
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {_DOCS_BASE: _create_resp("d1")}, captured
            ),
        )
        await plugin.call_tool("docs_create", {"title": "T"})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult(self):
        sentinel = "SENTINEL_DOCS_TOKEN_DO_NOT_LEAK"
        exc = DocsAPIError(
            f"401 Client Error for url: {_DRIVE_FILES} "
            f"(Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider(sentinel,
                                         refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({_DRIVE_FILES: exc}, []),
        )
        result = await plugin.call_tool("docs_list", {})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs(self, caplog):
        sentinel = "SENTINEL_DOCS_TOKEN_DO_NOT_LEAK"
        exc = DocsAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({_DRIVE_FILES: exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("docs_list", {})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 7 -- non-dict API response guards
# ---------------------------------------------------------------------------

class TestNonDictGuards:
    @pytest.mark.asyncio
    async def test_non_dict_list_response_is_error(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: [1, 2]}, []),
        )
        result = await plugin.call_tool("docs_list", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_read_response_is_error(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/documents/d1": "bad"}, []),
        )
        result = await plugin.call_tool("docs_read", {"document_id": "d1"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_create_response_is_error(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DOCS_BASE: [1, 2]}, []),
        )
        result = await plugin.call_tool("docs_create", {"title": "T"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_append_response_is_error(self):
        plugin = GoogleDocsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({":batchUpdate": "bad"}, []),
        )
        result = await plugin.call_tool("docs_append", {
            "document_id": "d1", "text": "x",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = GoogleDocsPlugin(token_provider=_StubProvider("t"),
                                  fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("docs_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'docs_bogus'" in result.content

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), GoogleDocsPlugin)

    @pytest.mark.asyncio
    async def test_no_account_is_lazy_error_for_create(self, monkeypatch):
        import plugins.google_docs as docs_mod
        monkeypatch.setattr(docs_mod, "_token_provider_factory",
                            lambda: None)
        plugin = create()
        result = await plugin.call_tool("docs_create", {"title": "T"})
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_no_account_is_lazy_error_for_append(self, monkeypatch):
        import plugins.google_docs as docs_mod
        monkeypatch.setattr(docs_mod, "_token_provider_factory",
                            lambda: None)
        plugin = create()
        result = await plugin.call_tool("docs_append", {
            "document_id": "d1", "text": "x",
        })
        assert result.is_error
        assert "no Google account connected" in result.content
