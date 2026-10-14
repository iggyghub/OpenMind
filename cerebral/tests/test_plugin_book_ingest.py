import asyncio

from plugins.book_ingest import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_book_list_requires_profile_id():
    # book_list's real, deterministic guard-clause path -- exercises the
    # actual code without needing a seeded book database.
    plugin = create()
    result = asyncio.run(plugin.call_tool("book_list", {}))
    assert result.is_error is True
    assert "profile_id" in result.content
