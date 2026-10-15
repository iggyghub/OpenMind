import asyncio
import json
import unittest.mock as mock
import pytest
from plugins.desktop_dialogs import create

@pytest.fixture
def plugin():
    run_mock = mock.Mock()
    spawn_mock = mock.Mock()
    user32_mock = mock.Mock()
    user32_mock.FindWindowW.return_value = 12345
    return create(run_fn=run_mock, spawn_fn=spawn_mock, user32=user32_mock)

def run_tool(plugin, name, args):
    return asyncio.run(plugin.call_tool(name, args))

def test_pick_folder_returns_path(plugin):
    plugin.run_fn.return_value = mock.Mock(stdout="C:\\Users\\Test\\Downloads")
    result = run_tool(plugin, "pick_folder", {})
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["path"] == "C:\\Users\\Test\\Downloads"

def test_pick_folder_cancel(plugin):
    plugin.run_fn.return_value = mock.Mock(stdout="")
    result = run_tool(plugin, "pick_folder", {})
    data = json.loads(result.content)
    assert data["canceled"] is True

def test_pick_save_path_default_name_passed(plugin):
    plugin.run_fn.return_value = mock.Mock(stdout="C:\\Users\\Test\\mydoc.txt")
    result = run_tool(plugin, "pick_save_path", {"default_name": "mydoc.txt"})
    data = json.loads(result.content)
    assert data["canceled"] is False
    call_args = plugin.run_fn.call_args[0][1]
    assert "mydoc.txt" in call_args

def test_show_in_folder_runs_explorer_select(plugin):
    with mock.patch.object(__import__("os"), "path", autospec=True) as mock_path:
        mock_path.exists.return_value = True
        result = run_tool(plugin, "show_in_folder", {"path": "/some/path"})
        data = json.loads(result.content)
        assert data["shown"] is True
        plugin.run_fn.assert_called_once_with(["explorer", "/select,/some/path"], capture_output=True)

def test_show_in_folder_missing_path_is_error(plugin):
    with mock.patch.object(__import__("os"), "path", autospec=True) as mock_path:
        mock_path.exists.return_value = False
        result = run_tool(plugin, "show_in_folder", {"path": "/nonexistent"})
        assert result.is_error is True

def test_message_box_spawns_and_validates_kind(plugin):
    result = run_tool(plugin, "message_box", {"kind": "warning", "message": "Test msg"})
    data = json.loads(result.content)
    assert data["shown"] is True
    spawn_call = plugin.spawn_fn.call_args[0][1]
    assert "showWarning" in spawn_call

def test_flash_attention_start_and_stop(plugin):
    result_start = run_tool(plugin, "flash_attention", {"start": True})
    data_start = json.loads(result_start.content)
    assert data_start["window_found"] is True
    assert data_start["flashing"] is True
    assert plugin.user32.FlashWindowEx.call_count == 1

    result_stop = run_tool(plugin, "flash_attention", {"start": False})
    data_stop = json.loads(result_stop.content)
    assert data_stop["window_found"] is True
    assert data_stop["flashing"] is False
    assert plugin.user32.FlashWindowEx.call_count == 2
