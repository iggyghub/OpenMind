import asyncio
import json
import sys
from unittest.mock import MagicMock
from plugins.desktop_dialogs import create

def test_pick_folder_returns_path():
    mock_run = MagicMock(return_value=MagicMock(stdout="C:\\fake\\path\n"))
    plugin = create(run_fn=mock_run)
    result = asyncio.run(plugin.call_tool("pick_folder", {"title": "Choose a folder"}))
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["path"] == "C:\\fake\\path"
    assert mock_run.call_args[0][0][2] == "Choose a folder"

def test_pick_folder_cancel():
    mock_run = MagicMock(return_value=MagicMock(stdout=""))
    plugin = create(run_fn=mock_run)
    result = asyncio.run(plugin.call_tool("pick_folder", {"title": "Cancel Test"}))
    data = json.loads(result.content)
    assert data["canceled"] is True

def test_pick_save_path_default_name_passed():
    mock_run = MagicMock(return_value=MagicMock(stdout="C:\\fake\\save.txt\n"))
    plugin = create(run_fn=mock_run)
    result = asyncio.run(plugin.call_tool("pick_save_path", {"default_name": "untitled.txt"}))
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["path"] == "C:\\fake\\save.txt"
    assert mock_run.call_args[0][0][2] == "untitled.txt"

def test_show_in_folder_runs_explorer_select():
    mock_run = MagicMock()
    plugin = create(run_fn=mock_run)
    result = asyncio.run(plugin.call_tool("show_in_folder", {"path": "C:\\fake\\dir"}))
    assert json.loads(result.content)["shown"] is True
    mock_run.assert_called_once_with(["explorer", "/select,C:\\fake\\dir"], capture_output=True)

def test_show_in_folder_missing_path_is_error():
    mock_run = MagicMock()
    plugin = create(run_fn=mock_run)
    result = asyncio.run(plugin.call_tool("show_in_folder", {"path": "nonexistent"}))
    assert result.is_error is True

def test_message_box_spawns_and_validates_kind():
    mock_spawn = MagicMock()
    plugin = create(spawn_fn=mock_spawn)
    result = asyncio.run(plugin.call_tool("message_box", {"kind": "info", "message": "Hello\\\"World"}))
    assert json.loads(result.content)["shown"] is True
    call_args = mock_spawn.call_args[0][0]
    assert call_args[3] == "Hello\\\"World"
    assert "Hello\\\"World" not in call_args[2]

def test_flash_attention_start_and_stop():
    mock_user32 = MagicMock()
    mock_user32.FindWindowW.return_value = 12345
    plugin = create(user32=mock_user32)
    
    asyncio.run(plugin.call_tool("flash_attention", {"start": True}))
    asyncio.run(plugin.call_tool("flash_attention", {"start": False}))
    
    assert mock_user32.FlashWindowEx.call_count == 2
    assert mock_user32.FlashWindowEx.call_args_list[0][0][0].dwFlags == 15
    assert mock_user32.FlashWindowEx.call_args_list[1][0][0].dwFlags == 0
