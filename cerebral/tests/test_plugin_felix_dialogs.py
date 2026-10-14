import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plugins.felix_dialogs import FelixDialogsPlugin


def _mock_run(return_data):
    class MockResult:
        returncode = 0
        stdout = json.dumps(return_data)
    return MockResult


def test_bridge_lists_both_tools():
    plugin = FelixDialogsPlugin(run_fn=_mock_run({"canceled": True}), repo_root=Path("/fake"))
    tools = plugin.create()
    assert len(tools) == 2
    assert tools[0].__name__ == "felix_dialog_open_path"
    assert tools[1].__name__ == "felix_dialog_save_path"


def test_open_returns_relative_path():
    plugin = FelixDialogsPlugin(run_fn=_mock_run({"canceled": False, "path": "folder/page.html"}), repo_root=Path("/fake"))
    res = plugin.felix_dialog_open_path()
    assert json.loads(res.content) == {"canceled": False, "path": "folder/page.html"}


def test_open_cancel():
    plugin = FelixDialogsPlugin(run_fn=_mock_run({"canceled": True}), repo_root=Path("/fake"))
    res = plugin.felix_dialog_open_path()
    assert json.loads(res.content) == {"canceled": True}


def test_open_outside_root_is_error_result():
    plugin = FelixDialogsPlugin(run_fn=_mock_run({"canceled": False, "error": "Must be inside the OpenMind folder."}), repo_root=Path("/fake"))
    res = plugin.felix_dialog_open_path()
    data = json.loads(res.content)
    assert data["canceled"] is False
    assert "error" in data


def test_save_returns_relative_path():
    plugin = FelixDialogsPlugin(run_fn=_mock_run({"canceled": False, "path": "new.html"}), repo_root=Path("/fake"))
    res = plugin.felix_dialog_save_path()
    assert json.loads(res.content) == {"canceled": False, "path": "new.html"}


def test_save_cancel_and_outside_root():
    plugin = FelixDialogsPlugin(run_fn=_mock_run({"canceled": True}), repo_root=Path("/fake"))
    res = plugin.felix_dialog_save_path()
    assert json.loads(res.content) == {"canceled": True}

    plugin2 = FelixDialogsPlugin(run_fn=_mock_run({"canceled": False, "error": "Must be inside the OpenMind folder."}), repo_root=Path("/fake"))
    res2 = plugin2.felix_dialog_save_path()
    assert json.loads(res2.content) == {"canceled": False, "error": "Must be inside the OpenMind folder."}
