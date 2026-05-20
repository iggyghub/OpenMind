"""
Notion MCP plugin tests -- Issue #136.

Tools (four):
  - notion_search (read, POST /v1/search)
  - notion_retrieve_page (read, GET /v1/pages/{id})
  - notion_retrieve_block_children (read, GET /v1/blocks/{id}/children)
  - notion_create_page (write, POST /v1/pages)

All hit the real Notion REST API v1 with a STATIC Internal Integration
Token from the NOTION_API_TOKEN env var via the provider seam. HTTP is
injected via fetch_fn and the token via a stub provider, so tests never
read os.environ, hit the keyring, or touch the network.

Learning-#15 substitution case (Bearer transport, plugin-specific value
= secret handling): the suite asserts the Bearer header IS attached AND
the token never reaches a log / ToolResult, INSTEAD of a fake-transport
regression test (the youtube/gmail/calendar/todoist precedent when
transport itself carries no plugin-specific value).

Notion-specific structural pin: every Notion API call MUST carry a
mandatory ``Notion-Version: 2022-06-28`` header alongside the bearer
header. The suite asserts both headers across every tool.

Protocol-narrowing divergence from gmail/calendar: Notion's
TokenProvider carries only current() (no refresh, no OAuth). A 401
propagates straight through with NO refresh-and-retry -- the suite
asserts exactly ONE outbound request on every error path.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.notion import (  # noqa: E402
    REQUIRED_CAPABILITIES,
    NotionAPIError,
    NotionPlugin,
    _build_create_body,
    _make_paragraph,
    _shape_block,
    _shape_object,
    create,
)

_BASE = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubProvider:
    """Static-token provider double. ``current()`` returns ``token`` (which
    can be ``None`` to model the never-set case). No refresh() -- the
    Protocol is one-method by design."""

    def __init__(self, token=None):
        self._token = token
        self.current_calls = 0

    def current(self):
        self.current_calls += 1
        return self._token


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


def _page(pid, *, title="Hello", url="https://www.notion.so/Hello-abc",
          parent=None, archived=False,
          created_time="2026-05-01T10:00:00.000Z",
          last_edited_time="2026-05-19T11:00:00.000Z"):
    """Build a canonical Notion page response object."""
    if parent is None:
        parent = {"type": "workspace", "workspace": True}
    return {
        "object": "page",
        "id": pid,
        "url": url,
        "created_time": created_time,
        "last_edited_time": last_edited_time,
        "parent": parent,
        "archived": archived,
        "properties": {
            "title": {
                "id": "title",
                "type": "title",
                "title": [
                    {"type": "text", "plain_text": title,
                     "text": {"content": title}},
                ],
            },
        },
    }


def _database(dbid, *, title="My DB", url="https://www.notion.so/My-DB-xyz"):
    return {
        "object": "database",
        "id": dbid,
        "url": url,
        "created_time": "2026-05-01T10:00:00.000Z",
        "last_edited_time": "2026-05-19T11:00:00.000Z",
        "parent": {"type": "workspace", "workspace": True},
        "archived": False,
        "title": [
            {"type": "text", "plain_text": title,
             "text": {"content": title}},
        ],
    }


def _text_block(bid, *, block_type="paragraph", text="hello world",
                has_children=False):
    """Build a canonical text-bearing block (paragraph by default)."""
    return {
        "object": "block",
        "id": bid,
        "type": block_type,
        "has_children": has_children,
        block_type: {
            "rich_text": [
                {"type": "text", "plain_text": text,
                 "text": {"content": text}},
            ],
        },
    }


def _nontext_block(bid, *, block_type="divider", has_children=False):
    return {
        "object": "block",
        "id": bid,
        "type": block_type,
        "has_children": has_children,
        block_type: {},
    }


_SEARCH_PAGE_RESPONSE = {
    "object": "list",
    "results": [
        _page("p-1", title="First"),
        _page("p-2", title="Second",
              parent={"type": "page_id", "page_id": "parent-1"}),
        _database("d-1", title="Tasks DB"),
    ],
    "next_cursor": "cursor-token",
    "has_more": True,
}

_PAGE_OK = _page("page-42", title="Project Alpha",
                 parent={"type": "page_id", "page_id": "workspace-root"})

_CREATED_PAGE = _page("new-1", title="Brand new page",
                      parent={"type": "page_id", "page_id": "parent-1"})

_BLOCKS_RESPONSE = {
    "object": "list",
    "results": [
        _text_block("b1", block_type="heading_1", text="A heading"),
        _text_block("b2", block_type="paragraph", text="A paragraph."),
        _text_block("b3", block_type="bulleted_list_item",
                    text="Bullet item", has_children=True),
        _nontext_block("b4", block_type="divider"),
        _nontext_block("b5", block_type="image"),
    ],
    "next_cursor": None,
    "has_more": False,
}


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_four_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "notion_search",
            "notion_retrieve_page",
            "notion_retrieve_block_children",
            "notion_create_page",
        }

    def test_create_plugin_named_notion(self):
        assert create().name == "notion"

    def test_required_capabilities(self):
        # secrets_read is a DELIBERATE over-declaration (gmail.py /
        # youtube.py / todoist.py posture-B); external_data_write is the
        # correct *required* ask-class semantic class for create_page.
        # Both external_data_* are hand-declared -- the per-file AST
        # audit maps neither.
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_search_has_no_required_args(self):
        tool = next(
            t for t in create().list_tools() if t.name == "notion_search"
        )
        assert tool.schema.get("required", []) == []
        for opt in ("query", "filter_type", "page_size", "start_cursor"):
            assert opt in tool.schema["properties"]

    def test_retrieve_page_requires_id(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "notion_retrieve_page"
        )
        assert tool.schema.get("required", []) == ["id"]
        assert "id" in tool.schema["properties"]

    def test_retrieve_block_children_requires_id(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "notion_retrieve_block_children"
        )
        assert tool.schema.get("required", []) == ["id"]
        for opt in ("id", "page_size", "start_cursor"):
            assert opt in tool.schema["properties"]

    def test_create_page_requires_parent_page_id_and_title(self):
        tool = next(
            t for t in create().list_tools() if t.name == "notion_create_page"
        )
        assert tool.schema.get("required", []) == ["parent_page_id", "title"]
        for opt in ("parent_page_id", "title", "content"):
            assert opt in tool.schema["properties"]


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no token (lazy error) + setter seam
# ---------------------------------------------------------------------------

class TestNoToken:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.notion as nt_mod
        monkeypatch.setattr(nt_mod, "_token_provider_factory", None)

        plugin = create()  # must not raise
        result = await plugin.call_tool("notion_search", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_token(self, monkeypatch):
        import plugins.notion as nt_mod
        monkeypatch.setattr(
            nt_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("notion_search", {})
        assert result.is_error
        assert "no Notion API token configured" in result.content

    @pytest.mark.parametrize("tool_name,args", [
        ("notion_search", {}),
        ("notion_retrieve_page", {"id": "p-1"}),
        ("notion_retrieve_block_children", {"id": "b-1"}),
        ("notion_create_page", {"parent_page_id": "p-1", "title": "x"}),
    ])
    @pytest.mark.asyncio
    async def test_no_token_lazy_error_across_tools(
            self, monkeypatch, tool_name, args):
        import plugins.notion as nt_mod
        monkeypatch.setattr(
            nt_mod, "_token_provider_factory", lambda: None
        )
        plugin = create()
        result = await plugin.call_tool(tool_name, dict(args))
        assert result.is_error
        assert "no Notion API token configured" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.notion as nt_mod
        prov = _StubProvider(token="tok")
        nt_mod.set_token_provider(lambda: prov)
        try:
            captured: list = []
            plugin = NotionPlugin(
                fetch_fn=_route_fetch(
                    {"search": _SEARCH_PAGE_RESPONSE}, captured,
                ),
            )
            result = await plugin.call_tool("notion_search", {})
            assert not result.is_error
        finally:
            monkeypatch.setattr(
                nt_mod, "_token_provider_factory", None
            )


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.parametrize("tool_name", [
        "notion_retrieve_page",
        "notion_retrieve_block_children",
    ])
    @pytest.mark.parametrize("bad_args", [
        {},
        {"id": ""},
        {"id": 12345},
    ])
    @pytest.mark.asyncio
    async def test_missing_id_is_error(self, tool_name, bad_args):
        plugin = NotionPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(tool_name, dict(bad_args))
        assert result.is_error
        assert "id" in result.content
        assert tool_name in result.content

    @pytest.mark.parametrize("bad_args,expected_missing", [
        ({}, ["parent_page_id", "title"]),
        ({"parent_page_id": "p1"}, ["title"]),
        ({"title": "x"}, ["parent_page_id"]),
        ({"parent_page_id": "", "title": "x"}, ["parent_page_id"]),
        ({"parent_page_id": "p1", "title": ""}, ["title"]),
        ({"parent_page_id": 1, "title": "x"}, ["parent_page_id"]),
        ({"parent_page_id": "p1", "title": 9}, ["title"]),
    ])
    @pytest.mark.asyncio
    async def test_create_page_missing_args(self, bad_args, expected_missing):
        plugin = NotionPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("notion_create_page", dict(bad_args))
        assert result.is_error
        for missing in expected_missing:
            assert missing in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- notion_search shaping, body, pagination passthrough
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_search_default_body_and_endpoint(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        result = await plugin.call_tool("notion_search", {})
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/search"
        # Default body carries page_size = 25; no query / filter on empty args
        assert call["json"]["page_size"] == 25
        assert "query" not in call["json"]
        assert "filter" not in call["json"]
        assert "start_cursor" not in call["json"]

    @pytest.mark.asyncio
    async def test_search_shapes_results(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, [],
            ),
        )
        result = await plugin.call_tool("notion_search", {})
        data = json.loads(result.content)
        assert data["next_cursor"] == "cursor-token"
        assert len(data["results"]) == 3
        # First page: workspace parent
        assert data["results"][0] == {
            "id": "p-1", "object": "page", "title": "First",
            "url": "https://www.notion.so/Hello-abc",
            "created_time": "2026-05-01T10:00:00.000Z",
            "last_edited_time": "2026-05-19T11:00:00.000Z",
            "parent_type": "workspace", "parent_id": "",
            "archived": False,
        }
        # Second page: page parent
        assert data["results"][1]["parent_type"] == "page_id"
        assert data["results"][1]["parent_id"] == "parent-1"
        # Database hit: title from top-level `title` array
        assert data["results"][2]["object"] == "database"
        assert data["results"][2]["title"] == "Tasks DB"

    @pytest.mark.asyncio
    async def test_search_query_forwarded(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"query": "alpha"})
        assert captured[0]["json"]["query"] == "alpha"

    @pytest.mark.parametrize("filter_type,expected", [
        ("page", {"value": "page", "property": "object"}),
        ("database", {"value": "database", "property": "object"}),
    ])
    @pytest.mark.asyncio
    async def test_search_filter_type_forwarded(self, filter_type, expected):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"filter_type": filter_type})
        assert captured[0]["json"]["filter"] == expected

    @pytest.mark.parametrize("bad_filter", ["block", "user", "", 7, None])
    @pytest.mark.asyncio
    async def test_search_invalid_filter_dropped(self, bad_filter):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"filter_type": bad_filter})
        assert "filter" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_search_page_size_clamped_low(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"page_size": 0})
        assert captured[0]["json"]["page_size"] == 1

    @pytest.mark.asyncio
    async def test_search_page_size_clamped_high(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"page_size": 500})
        assert captured[0]["json"]["page_size"] == 100

    @pytest.mark.asyncio
    async def test_search_page_size_default_is_25(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {})
        assert captured[0]["json"]["page_size"] == 25

    @pytest.mark.asyncio
    async def test_search_start_cursor_passthrough(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"start_cursor": "cur-abc"})
        assert captured[0]["json"]["start_cursor"] == "cur-abc"

    @pytest.mark.asyncio
    async def test_search_blank_start_cursor_dropped(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": _SEARCH_PAGE_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_search", {"start_cursor": ""})
        assert "start_cursor" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_search_non_dict_response_is_error(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"search": ["nope"]}, []),
        )
        result = await plugin.call_tool("notion_search", {})
        assert result.is_error
        assert "unexpected Notion search response" in result.content

    @pytest.mark.asyncio
    async def test_search_missing_results_empty_list(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": {"next_cursor": None}}, [],
            ),
        )
        result = await plugin.call_tool("notion_search", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["results"] == []
        assert data["next_cursor"] is None


# ---------------------------------------------------------------------------
# Cycle 5 -- notion_retrieve_page
# ---------------------------------------------------------------------------

class TestRetrievePage:
    @pytest.mark.asyncio
    async def test_retrieve_page_hits_correct_endpoint(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages/page-42": _PAGE_OK}, captured),
        )
        result = await plugin.call_tool("notion_retrieve_page", {
            "id": "page-42",
        })
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/pages/page-42"

    @pytest.mark.asyncio
    async def test_retrieve_page_shapes_page(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages/page-42": _PAGE_OK}, []),
        )
        result = await plugin.call_tool("notion_retrieve_page", {
            "id": "page-42",
        })
        data = json.loads(result.content)
        assert data["id"] == "page-42"
        assert data["object"] == "page"
        assert data["title"] == "Project Alpha"
        assert data["parent_type"] == "page_id"
        assert data["parent_id"] == "workspace-root"
        assert data["archived"] is False

    @pytest.mark.asyncio
    async def test_retrieve_page_missing_title_yields_empty_string(self):
        page = _page("p-blank", title="")
        # Strip the run entirely to model a truly empty title
        page["properties"]["title"]["title"] = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages/p-blank": page}, []),
        )
        result = await plugin.call_tool("notion_retrieve_page", {
            "id": "p-blank",
        })
        assert json.loads(result.content)["title"] == ""

    @pytest.mark.asyncio
    async def test_retrieve_page_multi_run_title_joined(self):
        page = _page("p-multi", title="Part A")
        page["properties"]["title"]["title"] = [
            {"type": "text", "plain_text": "Part A",
             "text": {"content": "Part A"}},
            {"type": "text", "plain_text": " — Part B",
             "text": {"content": " — Part B"}},
        ]
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages/p-multi": page}, []),
        )
        result = await plugin.call_tool("notion_retrieve_page", {
            "id": "p-multi",
        })
        assert json.loads(result.content)["title"] == "Part A — Part B"

    @pytest.mark.asyncio
    async def test_retrieve_page_archived_passthrough(self):
        page = _page("p-arch", title="Old", archived=True)
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages/p-arch": page}, []),
        )
        result = await plugin.call_tool("notion_retrieve_page", {
            "id": "p-arch",
        })
        assert json.loads(result.content)["archived"] is True

    @pytest.mark.asyncio
    async def test_retrieve_page_non_dict_response_is_error(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages/p-1": []}, []),
        )
        result = await plugin.call_tool("notion_retrieve_page", {
            "id": "p-1",
        })
        assert result.is_error
        assert "unexpected Notion retrieve_page response" in result.content


# ---------------------------------------------------------------------------
# Cycle 6 -- notion_retrieve_block_children
# ---------------------------------------------------------------------------

class TestRetrieveBlockChildren:
    @pytest.mark.asyncio
    async def test_endpoint_and_default_page_size(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/blocks/b-1/children": _BLOCKS_RESPONSE}, captured,
            ),
        )
        await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-1",
        })
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/blocks/b-1/children"
        assert call["params"]["page_size"] == 100

    @pytest.mark.asyncio
    async def test_blocks_shaped_with_plain_text(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/blocks/b-root/children": _BLOCKS_RESPONSE}, [],
            ),
        )
        result = await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-root",
        })
        data = json.loads(result.content)
        assert data["next_cursor"] is None
        blocks = data["blocks"]
        assert len(blocks) == 5
        assert blocks[0] == {
            "id": "b1", "type": "heading_1",
            "text": "A heading", "has_children": False,
        }
        assert blocks[1]["type"] == "paragraph"
        assert blocks[1]["text"] == "A paragraph."
        assert blocks[2]["type"] == "bulleted_list_item"
        assert blocks[2]["has_children"] is True
        # divider + image are non-text -> text==""
        assert blocks[3] == {
            "id": "b4", "type": "divider", "text": "", "has_children": False,
        }
        assert blocks[4] == {
            "id": "b5", "type": "image", "text": "", "has_children": False,
        }

    @pytest.mark.parametrize("block_type", [
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item",
        "to_do", "quote", "callout", "code",
    ])
    @pytest.mark.asyncio
    async def test_all_text_bearing_block_types_extracted(self, block_type):
        block = _text_block("b-x", block_type=block_type, text="payload!")
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/blocks/b-x/children": {
                "results": [block], "next_cursor": None,
            }}, []),
        )
        result = await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-x",
        })
        blocks = json.loads(result.content)["blocks"]
        assert blocks[0]["type"] == block_type
        assert blocks[0]["text"] == "payload!"

    @pytest.mark.parametrize("block_type", [
        "image", "embed", "divider", "child_page", "child_database",
        "video", "audio", "file", "bookmark", "equation",
        "table", "table_row", "breadcrumb", "table_of_contents",
        "link_preview", "synced_block", "template",
    ])
    @pytest.mark.asyncio
    async def test_unsupported_block_types_yield_empty_text(self, block_type):
        block = _nontext_block("b-y", block_type=block_type)
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/blocks/b-y/children": {
                "results": [block], "next_cursor": None,
            }}, []),
        )
        result = await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-y",
        })
        blocks = json.loads(result.content)["blocks"]
        assert blocks[0]["type"] == block_type
        assert blocks[0]["text"] == ""

    @pytest.mark.asyncio
    async def test_multi_run_rich_text_concatenated(self):
        block = {
            "object": "block",
            "id": "b-multi", "type": "paragraph", "has_children": False,
            "paragraph": {
                "rich_text": [
                    {"plain_text": "Hello, ", "type": "text",
                     "text": {"content": "Hello, "}},
                    {"plain_text": "world", "type": "text",
                     "text": {"content": "world"}},
                    {"plain_text": "!", "type": "text",
                     "text": {"content": "!"}},
                ],
            },
        }
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/blocks/b-multi/children": {
                "results": [block], "next_cursor": None,
            }}, []),
        )
        result = await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-multi",
        })
        assert json.loads(result.content)["blocks"][0]["text"] == \
            "Hello, world!"

    @pytest.mark.asyncio
    async def test_page_size_clamped(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/blocks/b-1/children": {
                "results": [], "next_cursor": None,
            }}, captured),
        )
        await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-1", "page_size": 999,
        })
        assert captured[0]["params"]["page_size"] == 100
        captured.clear()
        await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-1", "page_size": 0,
        })
        assert captured[0]["params"]["page_size"] == 1

    @pytest.mark.asyncio
    async def test_start_cursor_passthrough(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/blocks/b-1/children": {
                "results": [], "next_cursor": None,
            }}, captured),
        )
        await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-1", "start_cursor": "cur-x",
        })
        assert captured[0]["params"]["start_cursor"] == "cur-x"

    @pytest.mark.asyncio
    async def test_non_dict_response_is_error(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/blocks/b-1/children": "nope"}, []),
        )
        result = await plugin.call_tool("notion_retrieve_block_children", {
            "id": "b-1",
        })
        assert result.is_error
        assert "unexpected Notion block-children response" in result.content


# ---------------------------------------------------------------------------
# Cycle 7 -- notion_create_page
# ---------------------------------------------------------------------------

class TestCreatePage:
    @pytest.mark.asyncio
    async def test_create_minimal_posts_parent_and_title(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, captured),
        )
        result = await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "Brand new page",
        })
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/pages"
        body = call["json"]
        assert body["parent"] == {"page_id": "parent-1"}
        assert body["properties"]["title"]["title"][0]["text"]["content"] \
            == "Brand new page"
        assert "children" not in body

    @pytest.mark.asyncio
    async def test_create_with_single_chunk_content(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, captured),
        )
        await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "T",
            "content": "Just one paragraph.",
        })
        body = captured[0]["json"]
        assert len(body["children"]) == 1
        first = body["children"][0]
        assert first["type"] == "paragraph"
        assert first["paragraph"]["rich_text"][0]["text"]["content"] \
            == "Just one paragraph."

    @pytest.mark.asyncio
    async def test_create_content_split_on_double_newline(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, captured),
        )
        await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "T",
            "content": "First.\n\nSecond.\n\nThird.",
        })
        children = captured[0]["json"]["children"]
        assert len(children) == 3
        assert [c["paragraph"]["rich_text"][0]["text"]["content"]
                for c in children] == ["First.", "Second.", "Third."]

    @pytest.mark.asyncio
    async def test_create_empty_chunks_dropped(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, captured),
        )
        # Three consecutive newlines (\n\n\n\n) creates an empty chunk
        await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "T",
            "content": "Alpha\n\n\n\nBeta",
        })
        children = captured[0]["json"]["children"]
        assert len(children) == 2
        assert [c["paragraph"]["rich_text"][0]["text"]["content"]
                for c in children] == ["Alpha", "Beta"]

    @pytest.mark.asyncio
    async def test_create_empty_content_yields_no_children_key(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, captured),
        )
        await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "T",
            "content": "",
        })
        assert "children" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_create_non_string_content_ignored(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, captured),
        )
        await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "T",
            "content": 42,
        })
        assert "children" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_create_response_is_shaped_page(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": _CREATED_PAGE}, []),
        )
        result = await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "Brand new page",
        })
        data = json.loads(result.content)
        assert data["id"] == "new-1"
        assert data["title"] == "Brand new page"
        assert data["parent_type"] == "page_id"
        assert data["parent_id"] == "parent-1"

    @pytest.mark.asyncio
    async def test_create_non_dict_response_is_error(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/pages": "oops"}, []),
        )
        result = await plugin.call_tool("notion_create_page", {
            "parent_page_id": "parent-1",
            "title": "x",
        })
        assert result.is_error
        assert "unexpected Notion create_page response" in result.content


# ---------------------------------------------------------------------------
# Cycle 8 -- BEARER + Notion-Version headers + token scrub
# ---------------------------------------------------------------------------

class TestBearerAndVersionHeaders:
    @pytest.mark.parametrize("tool_name,args,route_needle,resp", [
        ("notion_search", {}, "search", _SEARCH_PAGE_RESPONSE),
        ("notion_retrieve_page", {"id": "p-1"},
         "/pages/p-1", _PAGE_OK),
        ("notion_retrieve_block_children", {"id": "b-1"},
         "/blocks/b-1/children", _BLOCKS_RESPONSE),
        ("notion_create_page",
         {"parent_page_id": "p-1", "title": "t"},
         "/pages", _CREATED_PAGE),
    ])
    @pytest.mark.asyncio
    async def test_bearer_and_version_headers_attached(
            self, tool_name, args, route_needle, resp):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("hdr-tok"),
            fetch_fn=_route_fetch({route_needle: resp}, captured),
        )
        await plugin.call_tool(tool_name, dict(args))
        headers = captured[0]["headers"]
        assert headers["Authorization"] == "Bearer hdr-tok"
        assert headers["Notion-Version"] == _VERSION

    @pytest.mark.parametrize("tool_name,args,route_needle", [
        ("notion_search", {}, "search"),
        ("notion_retrieve_page", {"id": "p-1"}, "/pages/p-1"),
        ("notion_retrieve_block_children", {"id": "b-1"},
         "/blocks/b-1/children"),
        ("notion_create_page", {"parent_page_id": "p-1", "title": "t"},
         "/pages"),
    ])
    @pytest.mark.asyncio
    async def test_token_never_in_toolresult_or_logs(
            self, caplog, tool_name, args, route_needle):
        sentinel = f"SENTINEL_{tool_name.upper()}_TOKEN"
        exc = NotionAPIError(
            f"401 Client Error -- Authorization: Bearer {sentinel}",
            status=401,
        )
        plugin = NotionPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({route_needle: exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            result = await plugin.call_tool(tool_name, dict(args))
        assert result.is_error
        assert sentinel not in result.content
        assert sentinel not in caplog.text
        assert "***" in result.content


# ---------------------------------------------------------------------------
# Cycle 9 -- 401 propagates with NO retry (Protocol narrowing)
# ---------------------------------------------------------------------------

class TestNoRetryOn401:
    @pytest.mark.parametrize("tool_name,args,route_needle", [
        ("notion_search", {}, "search"),
        ("notion_retrieve_page", {"id": "p-1"}, "/pages/p-1"),
        ("notion_retrieve_block_children", {"id": "b-1"},
         "/blocks/b-1/children"),
        ("notion_create_page", {"parent_page_id": "p-1", "title": "t"},
         "/pages"),
    ])
    @pytest.mark.asyncio
    async def test_401_propagates_no_retry(
            self, tool_name, args, route_needle):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {route_needle: NotionAPIError(
                    "401 Unauthorized", status=401)},
                captured,
            ),
        )
        result = await plugin.call_tool(tool_name, dict(args))
        assert result.is_error
        assert "Notion request failed" in result.content
        # Exactly ONE outbound request -- no refresh, no retry
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_provider_has_no_refresh_method(self):
        # Protocol structural check: the stub provider has only current(),
        # mirroring the production TokenProvider Protocol. This pins the
        # divergence from gmail/calendar.
        prov = _StubProvider("tok")
        assert hasattr(prov, "current")
        assert not hasattr(prov, "refresh")

    @pytest.mark.asyncio
    async def test_non_401_also_propagates_no_retry(self):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"search": NotionAPIError("500 Server Error", status=500)},
                captured,
            ),
        )
        result = await plugin.call_tool("notion_search", {})
        assert result.is_error
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Cycle 10 -- pure-function helpers (_build_create_body, _make_paragraph,
#             _shape_object, _shape_block)
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_make_paragraph_canonical_shape(self):
        block = _make_paragraph("hi")
        assert block == {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "hi"}},
                ],
            },
        }

    def test_build_create_body_no_content(self):
        body = _build_create_body("parent-1", "T", None)
        assert body["parent"] == {"page_id": "parent-1"}
        assert body["properties"]["title"]["title"][0]["text"]["content"] \
            == "T"
        assert "children" not in body

    def test_build_create_body_with_split_content(self):
        body = _build_create_body("parent-1", "T", "A\n\nB")
        assert len(body["children"]) == 2

    def test_shape_object_workspace_parent(self):
        obj = _page("p-w", title="workspace child")
        shaped = _shape_object(obj)
        assert shaped["parent_type"] == "workspace"
        assert shaped["parent_id"] == ""

    def test_shape_object_page_parent(self):
        obj = _page("p-p", title="page child",
                    parent={"type": "page_id", "page_id": "parent-x"})
        shaped = _shape_object(obj)
        assert shaped["parent_type"] == "page_id"
        assert shaped["parent_id"] == "parent-x"

    def test_shape_object_database_parent(self):
        obj = _page("p-d", title="db child",
                    parent={"type": "database_id",
                            "database_id": "db-1"})
        shaped = _shape_object(obj)
        assert shaped["parent_type"] == "database_id"
        assert shaped["parent_id"] == "db-1"

    def test_shape_object_non_dict_returns_empty(self):
        assert _shape_object("nope") == {}
        assert _shape_object(None) == {}

    def test_shape_block_non_dict_returns_empty(self):
        assert _shape_block("nope") == {}
        assert _shape_block(None) == {}

    def test_shape_block_text_with_missing_rich_text(self):
        block = {
            "id": "b-x", "object": "block", "type": "paragraph",
            "has_children": False,
            "paragraph": {},
        }
        shaped = _shape_block(block)
        assert shaped["text"] == ""

    def test_shape_block_text_with_non_list_rich_text(self):
        block = {
            "id": "b-x", "object": "block", "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": "not a list"},
        }
        shaped = _shape_block(block)
        assert shaped["text"] == ""


# ---------------------------------------------------------------------------
# Cycle 11 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = NotionPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("notion_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'notion_bogus'" in result.content

    @pytest.mark.parametrize("tool_name,args,route_needle,resp", [
        ("notion_search", {}, "search", _SEARCH_PAGE_RESPONSE),
        ("notion_retrieve_page", {"id": "p-1"},
         "/pages/p-1", _PAGE_OK),
        ("notion_retrieve_block_children", {"id": "b-1"},
         "/blocks/b-1/children", _BLOCKS_RESPONSE),
        ("notion_create_page",
         {"parent_page_id": "p-1", "title": "t"},
         "/pages", _CREATED_PAGE),
    ])
    @pytest.mark.asyncio
    async def test_dispatch_routes_each_tool(
            self, tool_name, args, route_needle, resp):
        captured: list = []
        plugin = NotionPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({route_needle: resp}, captured),
        )
        result = await plugin.call_tool(tool_name, dict(args))
        assert not result.is_error
        # Exactly one outbound request -- proves dispatch reached the method
        assert len(captured) == 1

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), NotionPlugin)

    def test_create_accepts_fetch_fn(self):
        async def stub(*a, **k):  # pragma: no cover - shape only
            return None
        plugin = create(fetch_fn=stub)
        assert plugin._fetch is stub
