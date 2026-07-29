"""
Files plugin tests -- edit_file (Issue #543).

Covers: unique replace, missing old_string (error), non-unique old_string
without replace_all (error), replace_all multi-occurrence replace.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.files import FilesPlugin


async def test_edit_file_unique_replace(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello world", encoding="utf-8")
    plugin = FilesPlugin()

    result = await plugin.call_tool("edit_file", {
        "path": str(f), "old_string": "world", "new_string": "there",
    })

    assert not result.is_error
    assert f.read_text(encoding="utf-8") == "hello there"
    summary = json.loads(result.content)
    assert summary == {"path": str(f), "replacements": 1}


async def test_edit_file_missing_old_string_errors(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello world", encoding="utf-8")
    plugin = FilesPlugin()

    result = await plugin.call_tool("edit_file", {
        "path": str(f), "old_string": "nope", "new_string": "there",
    })

    assert result.is_error
    assert f.read_text(encoding="utf-8") == "hello world"


async def test_edit_file_non_unique_without_replace_all_errors(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("foo bar foo", encoding="utf-8")
    plugin = FilesPlugin()

    result = await plugin.call_tool("edit_file", {
        "path": str(f), "old_string": "foo", "new_string": "baz",
    })

    assert result.is_error
    assert f.read_text(encoding="utf-8") == "foo bar foo"


async def test_edit_file_replace_all(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("foo bar foo", encoding="utf-8")
    plugin = FilesPlugin()

    result = await plugin.call_tool("edit_file", {
        "path": str(f), "old_string": "foo", "new_string": "baz",
        "replace_all": True,
    })

    assert not result.is_error
    assert f.read_text(encoding="utf-8") == "baz bar baz"
    summary = json.loads(result.content)
    assert summary == {"path": str(f), "replacements": 2}
