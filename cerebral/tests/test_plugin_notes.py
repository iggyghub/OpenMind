from plugins.notes import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_create_and_list_note_happy_path(tmp_path):
    import asyncio

    plugin = create(notes_dir=tmp_path, db_path=tmp_path / "notes.db")
    result = asyncio.run(plugin.call_tool("create_note", {"title": "Stub", "body": "hello"}))
    assert result.is_error is False

    listed = asyncio.run(plugin.call_tool("list_recent", {}))
    assert listed.is_error is False
    assert "Stub" in listed.content
