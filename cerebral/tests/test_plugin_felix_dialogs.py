import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest

from plugins.felix_dialogs import FelixDialogsPlugin


class FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_run_fn(path_rel: Optional[str] = None, canceled: bool = False) -> callable:
    def run_fn(*args, **kwargs):
        if canceled:
            return FakeProc(stdout=json.dumps({"canceled": True}))
        if path_rel is None or path_rel.startswith(".."):
            return FakeProc(stdout=json.dumps({"canceled": False, "error": "Must be inside the OpenMind folder."}))
        return FakeProc(stdout=json.dumps({"canceled": False, "path": path_rel}))
    return run_fn


@pytest.fixture
def plugin():
    return FelixDialogsPlugin(root=Path("/fake/repo"), run_fn=lambda *a, **kw: FakeProc(stdout="{}"))


def test_bridge_lists_both_tools(plugin: FelixDialogsPlugin):
    tools = plugin.list_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"felix_dialog_open_path", "felix_dialog_save_path"}


def test_open_returns_relative_path(plugin: FelixDialogsPlugin):
    plugin.run_fn = _make_run_fn("pages/index.html")
    result = asyncio.run(plugin.call_tool("felix_dialog_open_path", {}))
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["path"] == "pages/index.html"


def test_open_cancel(plugin: FelixDialogsPlugin):
    plugin.run_fn = _make_run_fn(canceled=True)
    result = asyncio.run(plugin.call_tool("felix_dialog_open_path", {}))
    data = json.loads(result.content)
    assert data["canceled"] is True


def test_open_outside_root_is_error_result(plugin: FelixDialogsPlugin):
    plugin.run_fn = _make_run_fn("../etc/passwd")
    result = asyncio.run(plugin.call_tool("felix_dialog_open_path", {}))
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["error"] == "Must be inside the OpenMind folder."


def test_save_returns_relative_path(plugin: FelixDialogsPlugin):
    plugin.run_fn = _make_run_fn("docs/new.html")
    result = asyncio.run(plugin.call_tool("felix_dialog_save_path", {}))
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["path"] == "docs/new.html"


def test_save_cancel_and_outside_root(plugin: FelixDialogsPlugin):
    plugin.run_fn = _make_run_fn(canceled=True)
    result = asyncio.run(plugin.call_tool("felix_dialog_save_path", {}))
    data = json.loads(result.content)
    assert data["canceled"] is True

    plugin.run_fn = _make_run_fn("..")
    result = asyncio.run(plugin.call_tool("felix_dialog_save_path", {}))
    data = json.loads(result.content)
    assert data["canceled"] is False
    assert data["error"] == "Must be inside the OpenMind folder."
