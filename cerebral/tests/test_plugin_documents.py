import asyncio

from plugins.documents import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_doc_list_happy_path():
    # A fresh plugin instance has no store/profile wired yet -- doc_list's
    # documented behavior for that state is an empty list, not an error.
    plugin = create()
    result = asyncio.run(plugin.call_tool("doc_list", {}))
    assert result.is_error is False
    assert "docs" in result.content
