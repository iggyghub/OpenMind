import asyncio

from plugins.files import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_create_and_read_file_happy_path(tmp_path):
    plugin = create()
    target = tmp_path / "stub.txt"

    create_result = asyncio.run(
        plugin.call_tool("create_file", {"path": str(target), "content": "hello"})
    )
    assert create_result.is_error is False

    read_result = asyncio.run(plugin.call_tool("read_file", {"path": str(target)}))
    assert read_result.is_error is False
    assert "hello" in read_result.content
