"""desktop_dialogs plugin tests (Signal rebuild). Every side effect is faked."""
import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.desktop_dialogs import REQUIRED_CAPABILITIES, create


def _plugin(stdout=""):
    run = mock.Mock(return_value=mock.Mock(stdout=stdout, stderr="", returncode=0))
    spawn = mock.Mock()
    user32 = mock.Mock()
    user32.FindWindowW.return_value = 12345
    return create(run_fn=run, spawn_fn=spawn, user32=user32)


def _call(plugin, name, args):
    return asyncio.run(plugin.call_tool(name, args))


def test_pick_folder_returns_path():
    p = _plugin(stdout="C:\\Users\\Test\\Downloads\n")
    data = json.loads(_call(p, "pick_folder", {"title": "Pick one"}).content)
    assert data == {"canceled": False, "path": "C:\\Users\\Test\\Downloads"}
    argv = p.run_fn.call_args[0][0]
    assert argv[1] == "-c" and argv[3] == "Pick one"   # title travels as argv
    assert "Pick one" not in argv[2]                    # never pasted into the code


def test_pick_folder_cancel():
    data = json.loads(_call(_plugin(stdout="\n"), "pick_folder", {}).content)
    assert data == {"canceled": True}


def test_pick_save_path_default_name_passed():
    p = _plugin(stdout="C:\\docs\\mydoc.txt\n")
    data = json.loads(_call(p, "pick_save_path", {"default_name": "mydoc.txt"}).content)
    assert data == {"canceled": False, "path": "C:\\docs\\mydoc.txt"}
    argv = p.run_fn.call_args[0][0]
    assert argv[3] == "mydoc.txt" and "mydoc.txt" not in argv[2]
    assert _call(p, "pick_save_path", {"default_name": "..\\evil.txt"}).is_error


def test_show_in_folder_runs_explorer_select(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    p = _plugin()
    data = json.loads(_call(p, "show_in_folder", {"path": str(target)}).content)
    assert data == {"shown": True}
    assert p.run_fn.call_args[0][0] == ["explorer", "/select," + str(target)]


def test_show_in_folder_missing_path_is_error(tmp_path):
    p = _plugin()
    assert _call(p, "show_in_folder", {"path": str(tmp_path / "nope")}).is_error
    assert _call(p, "show_in_folder", {}).is_error
    p.run_fn.assert_not_called()


def test_message_box_spawns_and_validates_kind():
    p = _plugin()
    tricky = "it's a \\' test\nwith __import__('os')"
    data = json.loads(_call(p, "message_box", {"kind": "warning", "message": tricky}).content)
    assert data == {"shown": True}
    argv = p.spawn_fn.call_args[0][0]
    assert argv[3] == tricky                 # message arrives as an argv item
    assert tricky not in argv[2]             # and is not inside the code string
    assert "showwarning" in argv[2]
    assert _call(p, "message_box", {"kind": "bogus", "message": "x"}).is_error
    assert _call(p, "message_box", {"kind": "info", "message": "x" * 2001}).is_error


def test_flash_attention_start_and_stop():
    p = _plugin()
    start = json.loads(_call(p, "flash_attention", {"start": True}).content)
    assert start == {"window_found": True, "flashing": True}
    stop = json.loads(_call(p, "flash_attention", {"start": False}).content)
    assert stop == {"window_found": True, "flashing": False}
    assert p.user32.FlashWindowEx.call_count == 2
    p.user32.FindWindowW.return_value = 0
    assert json.loads(_call(p, "flash_attention", {}).content) == {"window_found": False, "flashing": False}
    assert REQUIRED_CAPABILITIES == frozenset({"fs_read", "fs_write", "device_control"})


def test_flashwinfo_hwnd_is_pointer_sized():
    # Regression: a 32-bit hwnd field gives the wrong struct size on 64-bit Windows.
    import plugins.desktop_dialogs as dd
    src = Path(dd.__file__).read_text(encoding="utf-8")
    assert '("hwnd", ctypes.c_void_p)' in src
