import asyncio
import json
import pytest
from plugins.autolaunch import create


@pytest.fixture
def plugin(tmp_path):
    return create(startup_dir=tmp_path, root=tmp_path)


def test_status_disabled_by_default(plugin):
    result = asyncio.run(plugin.call_tool("autolaunch_status", {}))
    data = json.loads(result.content)
    assert data["enabled"] is False
    assert "path" in data


def test_enable_writes_launcher_cmd(plugin):
    result = asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": True}))
    data = json.loads(result.content)
    assert data["enabled"] is True
    assert plugin.launcher_file.exists()
    content = plugin.launcher_file.read_text()
    lines = content.splitlines()
    assert lines[0] == "@echo off"
    assert "powershell.exe" in lines[1]


def test_disable_removes_it(plugin):
    asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": True}))
    assert plugin.launcher_file.exists()
    result = asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": False}))
    data = json.loads(result.content)
    assert data["enabled"] is False
    assert not plugin.launcher_file.exists()


def test_enable_is_idempotent(plugin):
    asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": True}))
    first_content = plugin.launcher_file.read_text()
    asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": True}))
    second_content = plugin.launcher_file.read_text()
    assert first_content == second_content


def test_requires_boolean(plugin):
    result = asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": "yes"}))
    assert result.is_error is True
    result2 = asyncio.run(plugin.call_tool("autolaunch_set", {"enabled": 1}))
    assert result2.is_error is True
