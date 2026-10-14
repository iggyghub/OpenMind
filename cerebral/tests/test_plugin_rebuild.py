"""Rebuild plugin tests (ADR-0040, RB1): spec extraction from a REA run."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.rebuild import REQUIRED_CAPABILITIES, RebuildPlugin, create, facts_from_graph


def _obs(label, props, confidence="exact", source="main.js", line=10):
    return {
        "label": label,
        "properties": props,
        "evidence": {
            "confidence": confidence,
            "location": {"value": {"source": source, "start": {"line": line}}},
        },
    }


def _graph():
    return {"nodes": [
        {"node_id": "n-win", "kind": "browser-window", "observations": [_obs(
            "BrowserWindow@1:1", {"web_preferences": [
                {"name": "contextIsolation", "value": {"value": True}},
                {"name": "nodeIntegration", "value": {"value": False}}],
                "preload_path": "preload/x.js"})]},
        {"node_id": "n-ch", "kind": "ipc-channel", "observations": [
            _obs("app:save", {"side": "main", "operation": "handle"}),
            _obs("app:save", {"side": "renderer", "operation": "invoke"})]},
        {"node_id": "n-h", "kind": "ipc-handler", "observations": [
            _obs("app:save", {"handler_kind": "inline-function"})]},
        {"node_id": "n-br", "kind": "context-bridge-api", "observations": [
            _obs("api", {"members": ["a", "b"], "unknown_members": 1})]},
        {"node_id": "n-ep", "kind": "endpoint", "observations": [
            _obs("help", {"endpoint_kind": "route", "value": "help"})]},
        {"node_id": "n-st", "kind": "storage", "observations": [
            _obs("local-storage", {"storage_kind": "local-storage", "name": None})]},
        {"node_id": "n-mod", "kind": "javascript-module", "observations": []},
    ]}


def _call(plugin, args):
    return asyncio.run(plugin.call_tool("rebuild_new", args))


def test_name_and_capabilities():
    assert create().name == "rebuild"
    assert isinstance(REQUIRED_CAPABILITIES, frozenset) and REQUIRED_CAPABILITIES
    assert [t.name for t in create().list_tools()] == ["rebuild_new", "rebuild_score"]


def test_facts_cover_each_kind_with_stable_ids_and_skip_modules():
    facts = facts_from_graph(_graph())
    assert sorted(f["id"] for f in facts) == [
        "F-bridge-api-1", "F-endpoint-1", "F-ipc-channel-1", "F-ipc-handler-1",
        "F-storage-1", "F-window-1",
    ]
    by_kind = {f["kind"]: f for f in facts}
    assert by_kind["window"]["detail"]["web_preferences"] == {
        "contextIsolation": True, "nodeIntegration": False}
    assert by_kind["window"]["detail"]["preload_path"] == "preload/x.js"
    assert by_kind["ipc-channel"]["detail"] == {"sides": ["main", "renderer"],
                                                "operations": ["handle", "invoke"]}
    assert by_kind["bridge-api"]["detail"] == {"members": ["a", "b"], "unknown_members": 1}
    assert by_kind["window"]["source"] == "main.js:10"
    assert by_kind["window"]["confidence"] == "exact"


def test_facts_are_deterministic_regardless_of_node_order():
    g = _graph()
    shuffled = {"nodes": list(reversed(g["nodes"]))}
    assert facts_from_graph(g) == facts_from_graph(shuffled)


def test_strings_from_the_app_are_length_capped():
    g = {"nodes": [{"node_id": "n", "kind": "endpoint", "observations": [
        _obs("x" * 5000, {"endpoint_kind": "route", "value": "y" * 5000})]}]}
    fact = facts_from_graph(g)[0]
    assert len(fact["label"]) == 200 and len(fact["detail"]["value"]) == 200


def _make_run(tmp_path, graph, run_id="0123456789ab"):
    runs = tmp_path / "runs"
    runs.mkdir()
    doc = {"subject": {"name": "C:/app"}, "confidence": "derived",
           "limitations": ["static only"], "normalized_result": {"graph": graph}}
    (runs / f"{run_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    return runs


def test_rebuild_new_writes_spec(tmp_path):
    runs = _make_run(tmp_path, _graph())
    plugin = RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb")
    res = _call(plugin, {"name": "demo", "run_id": "0123456789ab", "feature": "save dialog"})
    assert not res.is_error
    out = json.loads(res.content)
    assert out["fact_total"] == 6 and out["counts"]["window"] == 1
    spec = json.loads(Path(out["spec_path"]).read_text(encoding="utf-8"))
    assert spec["name"] == "demo" and spec["feature"] == "save dialog"
    assert spec["source"] == {"run_id": "0123456789ab", "target": "C:/app", "confidence": "derived"}
    assert spec["limitations"] == ["static only"]
    assert len(spec["facts"]) == 6


@pytest.mark.parametrize("name", ["", "Demo", "../x", "a b", "-x", "x" * 41, None])
def test_bad_names_rejected(tmp_path, name):
    runs = _make_run(tmp_path, _graph())
    res = _call(RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb"),
                {"name": name, "run_id": "0123456789ab"})
    assert res.is_error


# ---------------------------------------------------------------------------
# RB1b -- scoped specs
# ---------------------------------------------------------------------------

def test_scope_keeps_matching_facts_with_stable_ids(tmp_path):
    runs = _make_run(tmp_path, _graph())
    plugin = RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb")
    full = json.loads(Path(json.loads(_call(plugin, {"name": "full", "run_id": "0123456789ab"}).content)
                           ["spec_path"]).read_text(encoding="utf-8"))
    res = _call(plugin, {"name": "part", "run_id": "0123456789ab", "scope": ["APP:SAVE", "api"]})
    assert not res.is_error
    spec = json.loads(Path(json.loads(res.content)["spec_path"]).read_text(encoding="utf-8"))
    assert spec["scope"] == ["app:save", "api"]
    ids = sorted(f["id"] for f in spec["facts"])
    assert ids == ["F-bridge-api-1", "F-ipc-channel-1", "F-ipc-handler-1"]
    full_by_id = {f["id"]: f for f in full["facts"]}
    assert all(full_by_id[f["id"]]["label"] == f["label"] for f in spec["facts"])


@pytest.mark.parametrize("scope", ["app", [""], [1], ["x"] * 11, ["y" * 81]])
def test_bad_scope_rejected(tmp_path, scope):
    runs = _make_run(tmp_path, _graph())
    res = _call(RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb"),
                {"name": "demo", "run_id": "0123456789ab", "scope": scope})
    assert res.is_error and "scope" in res.content


def test_no_scope_means_everything(tmp_path):
    runs = _make_run(tmp_path, _graph())
    res = _call(RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb"),
                {"name": "demo", "run_id": "0123456789ab"})
    spec = json.loads(Path(json.loads(res.content)["spec_path"]).read_text(encoding="utf-8"))
    assert len(spec["facts"]) == 6 and spec["scope"] is None
    assert not (tmp_path / "rb").exists()


@pytest.mark.parametrize("run_id", ["", "../../etc/passwd", "ZZZZZZZZZZZZ", "0123", None])
def test_bad_run_ids_rejected(tmp_path, run_id):
    runs = _make_run(tmp_path, _graph())
    res = _call(RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb"),
                {"name": "demo", "run_id": run_id})
    assert res.is_error


def test_missing_run_and_run_without_graph(tmp_path):
    runs = _make_run(tmp_path, _graph())
    plugin = RebuildPlugin(run_dir=runs, rebuild_dir=tmp_path / "rb")
    assert _call(plugin, {"name": "demo", "run_id": "ffffffffffff"}).is_error
    (runs / "aaaaaaaaaaaa.json").write_text(json.dumps({"normalized_result": {}}), encoding="utf-8")
    res = _call(plugin, {"name": "demo", "run_id": "aaaaaaaaaaaa"})
    assert res.is_error and "no application graph" in res.content


# ---------------------------------------------------------------------------
# RB2 -- rebuild_score
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock


def _spec_dir(tmp_path, facts):
    d = tmp_path / "rb" / "demo"
    d.mkdir(parents=True)
    (d / "spec.json").write_text(json.dumps({"facts": facts}), encoding="utf-8")
    return tmp_path / "rb"


def _fact(fid, kind="ipc-channel", confidence="exact", label="x"):
    return {"id": fid, "kind": kind, "label": label, "confidence": confidence}


def _repo(tmp_path, manifest=None, test_files=("cerebral/tests/test_demo.py",)):
    root = tmp_path / "repo"
    for rel in test_files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def test_a():\n    pass\n", encoding="utf-8")
    if manifest is not None:
        m = root / "plugins" / "rebuild_manifests"
        m.mkdir(parents=True, exist_ok=True)
        (m / "demo.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _score(plugin):
    return asyncio.run(plugin.call_tool("rebuild_score", {"name": "demo"}))


def _runner(returncode=0):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return MagicMock(returncode=returncode, stdout="", stderr="")

    return run, calls


def test_score_met_unmet_unknown_waived(tmp_path):
    rb = _spec_dir(tmp_path, [
        _fact("F-a"), _fact("F-b"), _fact("F-c", confidence="derived"), _fact("F-d"),
    ])
    repo = _repo(tmp_path, {
        "implements": {"F-a": {"symbol": "plugins/x.py::tool",
                               "test": "cerebral/tests/test_demo.py::test_a"}},
        "waived": {"F-d": "not applicable to a panel"},
    })
    run, calls = _runner(0)
    res = _score(RebuildPlugin(rebuild_dir=rb, repo_root=repo, run_fn=run))
    out = json.loads(res.content)
    assert out["counts"] == {"met": 1, "unmet": 1, "unknown": 1, "waived": 1}
    assert out["percent_met"] == 50.0
    assert [u["id"] for u in out["unmet"]] == ["F-b"]
    assert len(calls) == 1 and calls[0][-1] == "cerebral/tests/test_demo.py::test_a"
    card = json.loads((rb / "demo" / "scorecard.json").read_text(encoding="utf-8"))
    assert card["total"] == 4 and "behaves the same" in card["note"]


def test_failing_test_is_unmet(tmp_path):
    rb = _spec_dir(tmp_path, [_fact("F-a")])
    repo = _repo(tmp_path, {"implements": {"F-a": {
        "symbol": "s", "test": "cerebral/tests/test_demo.py::test_a"}}})
    run, _ = _runner(1)
    out = json.loads(_score(RebuildPlugin(rebuild_dir=rb, repo_root=repo, run_fn=run)).content)
    assert out["counts"]["unmet"] == 1 and out["counts"]["met"] == 0


@pytest.mark.parametrize("entry", [
    {"symbol": "s"},                                                    # no test
    {"test": "cerebral/tests/test_demo.py::test_a"},                   # no symbol
    {"symbol": "s", "test": "cerebral/tests/../../etc/passwd.py::x"},  # traversal
    {"symbol": "s", "test": "-k evil"},                                # flag injection
    {"symbol": "s", "test": "other/dir/test_x.py::test_a"},            # outside tests dir
    {"symbol": "s", "test": "cerebral/tests/test_missing.py::test_a"}, # file missing
])
def test_bad_manifest_entries_are_unmet_and_never_run(tmp_path, entry):
    rb = _spec_dir(tmp_path, [_fact("F-a")])
    repo = _repo(tmp_path, {"implements": {"F-a": entry}})
    run, calls = _runner(0)
    out = json.loads(_score(RebuildPlugin(rebuild_dir=rb, repo_root=repo, run_fn=run)).content)
    assert out["counts"]["unmet"] == 1 and calls == []


def test_missing_manifest_and_missing_spec(tmp_path):
    rb = _spec_dir(tmp_path, [_fact("F-a")])
    run, calls = _runner(0)
    plugin = RebuildPlugin(rebuild_dir=rb, repo_root=_repo(tmp_path), run_fn=run)
    out = json.loads(_score(plugin).content)
    assert out["counts"]["unmet"] == 1 and calls == []
    res = asyncio.run(RebuildPlugin(rebuild_dir=tmp_path / "none", run_fn=run)
                      .call_tool("rebuild_score", {"name": "demo"}))
    assert res.is_error and "no spec" in res.content


def test_score_rejects_bad_name(tmp_path):
    res = asyncio.run(RebuildPlugin(rebuild_dir=tmp_path).call_tool("rebuild_score", {"name": "../x"}))
    assert res.is_error
