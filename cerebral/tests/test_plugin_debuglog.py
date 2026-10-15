import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from plugins.debuglog import DebuglogPlugin, fetch_log_entries, build_report


class FakeRunResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def temp_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "cerebral.log").write_text("line1\ncline2\nclast\n", encoding="utf-8")
        (root / "cerebral.err.log").write_text("err1\nerrlast\n", encoding="utf-8")
        yield root


def test_fetch_reads_tail_of_each_log(temp_root):
    entries = fetch_log_entries(temp_root, max_lines=2)
    assert len(entries) == 2
    assert entries[0]["file"] == "cerebral.log"
    assert entries[0]["lines"] == ["cline2\n", "clast\n"]
    assert entries[1]["file"] == "cerebral.err.log"
    assert entries[1]["lines"] == ["err1\n", "errlast\n"]


def test_fetch_skips_missing_logs(temp_root):
    entries = fetch_log_entries(temp_root, max_lines=10)
    assert len(entries) == 2
    names = [e["file"] for e in entries]
    assert "launcher.log" not in names


def test_report_has_environment_header(temp_root):
    entries = fetch_log_entries(temp_root)
    versions = {"Python": "3.11", "Platform": "test", "Architecture": "amd64", "GitCommit": "deadbeef"}
    report = build_report(entries, versions)
    assert report.startswith("Felix debug log\n")
    assert "Python: 3.11" in report
    assert "GitCommit: deadbeef" in report
    assert "\n== cerebral.log ==\n" in report


def test_save_writes_chosen_path(temp_root):
    save_path = str(temp_root / "saved_debug.txt")
    plugin = DebuglogPlugin(root=temp_root, run_fn=lambda *args, **kwargs: FakeRunResult(stdout=save_path + "\n"))
    res = asyncio.run(plugin.call_tool("debuglog_save", {"content": "Hello World\n"}))
    assert not res.is_error
    data = json.loads(res.content)
    assert data["canceled"] is False
    assert data["path"] == save_path
    assert Path(save_path).read_text(encoding="utf-8") == "Hello World\n"


def test_save_cancel(temp_root):
    plugin = DebuglogPlugin(root=temp_root, run_fn=lambda *args, **kwargs: FakeRunResult(stdout=""))
    res = asyncio.run(plugin.call_tool("debuglog_save", {"content": "Hello"}))
    assert not res.is_error
    data = json.loads(res.content)
    assert data["canceled"] is True


def test_panel_has_refresh_and_report(temp_root):
    plugin = DebuglogPlugin(root=temp_root)
    spec = plugin.panel_spec()
    assert spec["title"] == "Debug log"
    widgets = spec["widgets"]
    assert len(widgets) == 3
    assert widgets[0]["type"] == "detail"
    assert "files_read" in widgets[0]["fields"]
    assert "line_count" in widgets[0]["fields"]
    assert widgets[1]["type"] == "action"
    assert widgets[1]["id"] == "debuglog-refresh"
    assert widgets[1]["tool"] == "debuglog_fetch"
    assert widgets[2]["type"] == "text"
    assert widgets[2]["id"] == "debuglog-report"
    assert widgets[2]["tool"] == "debuglog_save"
    assert "Felix debug log" in widgets[2]["value"]
