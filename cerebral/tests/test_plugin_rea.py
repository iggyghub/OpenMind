"""REA plugin tests (#1420). The `rea` CLI is always injected -- no npx/network."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.rea import REA_PINNED_VERSION, REQUIRED_CAPABILITIES, ReaPlugin, _node_ok, create


def _runner(stdout_by_cmd=None, returncode=0, stderr=""):
    """Fake run_fn: looks up stdout by the first non-exe argv token."""
    calls = []
    table = stdout_by_cmd or {}

    def run(argv, **kwargs):
        calls.append(argv)
        key = argv[1] if len(argv) > 1 else argv[0]
        return MagicMock(stdout=table.get(key, ""), stderr=stderr, returncode=returncode)

    return run, calls


def _call(plugin, tool, args=None):
    return asyncio.run(plugin.call_tool(tool, args or {}))


def test_required_capabilities_and_name():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset) and REQUIRED_CAPABILITIES
    assert create().name == "rea"
    assert {t.name for t in create().list_tools()} == {"rea_status", "rea_analyze_javascript"}


@pytest.mark.parametrize(
    "v,ok",
    [("v22.19.0", True), ("v22.18.9", False), ("v24.8.0", False), ("v24.11.0", True),
     ("v25.1.0", False), ("v26.8.1", True), ("garbage", False)],
)
def test_node_version_gate(v, ok):
    assert _node_ok(v) is ok


def test_status_ready():
    run, _ = _runner({"--version": "v26.8.1"})
    # node and rea both answer to --version; distinguish by argv[0]
    def run2(argv, **kw):
        return MagicMock(stdout="v26.8.1" if argv[0] == "node" else REA_PINNED_VERSION,
                         stderr="", returncode=0)
    res = _call(ReaPlugin(run_fn=run2), "rea_status")
    data = json.loads(res.content)
    assert data["ready"] is True and data["problems"] == []


def test_status_reports_old_node_and_missing_rea():
    def run(argv, **kw):
        if argv[0] == "node":
            return MagicMock(stdout="v24.8.0", stderr="", returncode=0)
        raise FileNotFoundError("rea")
    data = json.loads(_call(ReaPlugin(run_fn=run), "rea_status").content)
    assert data["ready"] is False
    assert any("too old" in p for p in data["problems"])
    assert any("`rea` not found" in p for p in data["problems"])


@pytest.mark.parametrize("bad", ["", None, "--help", "C:\\a&calc", "a|b", 'a"b', "x\ny"])
def test_analyze_rejects_bad_paths(bad, tmp_path):
    run, calls = _runner()
    res = _call(ReaPlugin(run_fn=run, run_dir=tmp_path), "rea_analyze_javascript", {"path": bad})
    assert res.is_error and calls == []


def test_analyze_missing_path(tmp_path):
    run, calls = _runner()
    res = _call(ReaPlugin(run_fn=run, run_dir=tmp_path), "rea_analyze_javascript",
                {"path": str(tmp_path / "nope")})
    assert res.is_error and calls == []


def test_analyze_saves_full_result_and_returns_summary(tmp_path):
    target = tmp_path / "app"
    target.mkdir()
    doc = {
        "operation": "analyze_javascript_application",
        "subject": {"name": str(target)},
        "confidence": "medium",
        "authority": "derived",
        "limitations": ["static only"],
        "normalized_result": {
            "format": "directory",
            "summary": {"browser_windows": 1},
            "statistics": {"relevant_files": 3},
            "semantic_graph": {"nodes": ["x"] * 1000},
        },
    }
    stdout = json.dumps(doc)

    def run(argv, **kw):
        if argv[1] == "--version":
            return MagicMock(stdout=REA_PINNED_VERSION, stderr="", returncode=0)
        return MagicMock(stdout=stdout, stderr='{"rea_progress": {}}', returncode=0)

    runs = tmp_path / "runs"
    res = _call(ReaPlugin(run_fn=run, run_dir=runs), "rea_analyze_javascript", {"path": str(target)})
    assert not res.is_error
    out = json.loads(res.content)
    assert out["summary"] == {"browser_windows": 1}
    assert out["statistics"] == {"relevant_files": 3}
    assert out["limitations"] == ["static only"]
    assert out["rea_version"] == REA_PINNED_VERSION
    assert "semantic_graph" not in res.content  # the big graph never reaches the model
    saved = Path(out["full_result_path"])
    assert json.loads(saved.read_text(encoding="utf-8")) == doc
    assert (runs / f"{out['run_id']}.summary.json").exists()


def test_analyze_nonzero_exit_and_bad_json(tmp_path):
    target = tmp_path / "a"
    target.mkdir()
    run, _ = _runner(returncode=2, stderr="boom")
    res = _call(ReaPlugin(run_fn=run, run_dir=tmp_path), "rea_analyze_javascript", {"path": str(target)})
    assert res.is_error and "boom" in res.content
    run2, _ = _runner({"analyze-javascript-application": "not json"})
    res2 = _call(ReaPlugin(run_fn=run2, run_dir=tmp_path), "rea_analyze_javascript", {"path": str(target)})
    assert res2.is_error


def _fake_cli():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = "v26.8.1" if argv[0] == "node" else REA_PINNED_VERSION
        return MagicMock(stdout=out, stderr="", returncode=0)

    return run, calls


def test_panel_spec_ready_and_form(tmp_path):
    run, _ = _fake_cli()
    spec = ReaPlugin(run_fn=run, run_dir=tmp_path).panel_spec()
    assert spec["title"] == "REA"
    types = [w["type"] for w in spec["widgets"]]
    assert types == ["detail", "action"]  # no problems list, no runs group
    detail = spec["widgets"][0]
    assert {"label": "Ready", "value": "yes"} in detail["fields"]
    action = spec["widgets"][1]
    assert action["tool"] == "rea_analyze_javascript" and action["input_arg"] == "path"


def test_panel_spec_lists_problems_and_recent_runs(tmp_path):
    (tmp_path / "a.summary.json").write_text(json.dumps({
        "run_id": "a", "target": "C:/app", "ts": "2026-10-13T19:08:17+00:00",
        "confidence": "derived", "statistics": {"relevant_files": 36, "findings": 54},
    }), encoding="utf-8")
    (tmp_path / "bad.summary.json").write_text("{not json", encoding="utf-8")

    def run(argv, **kw):
        if argv[0] == "node":
            return MagicMock(stdout="v24.8.0", stderr="", returncode=0)
        raise FileNotFoundError("rea")

    spec = ReaPlugin(run_fn=run, run_dir=tmp_path).panel_spec()
    types = [w["type"] for w in spec["widgets"]]
    assert types == ["detail", "list", "action", "group"]
    assert any("too old" in i["title"] for i in spec["widgets"][1]["items"])
    runs = spec["widgets"][3]["widgets"][0]["items"]
    assert len(runs) == 1 and runs[0]["title"] == "C:/app"
    assert "36 files" in runs[0]["subtitle"] and "54 findings" in runs[0]["subtitle"]


def test_panel_spec_status_probe_is_cached(tmp_path):
    run, calls = _fake_cli()
    plugin = ReaPlugin(run_fn=run, run_dir=tmp_path)
    plugin.panel_spec()
    n = len(calls)
    plugin.panel_spec()
    assert len(calls) == n  # second render did not shell out again
