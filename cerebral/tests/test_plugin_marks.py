"""plugins/marks.py -- the .marks/ store behind mark mode (mark-kit M4)."""
import base64
import json

import pytest

from plugins import marks as M

PNG = M._PNG_MAGIC + b"rest-of-a-png"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text("abc123\n")
    (tmp_path / ".gitignore").write_text("node_modules/")
    return tmp_path


async def call(name, **args):
    res = await M.create().call_tool(name, args)
    return res.is_error, (json.loads(res.content) if not res.is_error else res.content)


async def test_add_stores_mark_shot_commit_gitignore_and_is_idempotent(repo):
    shot = "data:image/png;base64," + base64.b64encode(PNG).decode()
    err, m = await call("marks_add", repo=str(repo), shot=shot,
                        mark={"id": "m1", "note": "too big", "selector": "#a", "shot": "dropped"})
    assert not err
    assert (m["status"], m["commit"], m["shot_path"]) == ("open", "abc123", ".marks/shots/m1.png")
    assert (repo / ".marks" / "shots" / "m1.png").read_bytes() == PNG
    assert (repo / ".gitignore").read_text().splitlines() == ["node_modules/", ".marks/"]
    line = json.loads((repo / ".marks" / "marks.jsonl").read_text())
    assert "shot" not in line and line["note"] == "too big"

    err, again = await call("marks_add", repo=str(repo), mark={"id": "m1", "note": "dup"})
    assert again["note"] == "too big"
    assert len((repo / ".marks" / "marks.jsonl").read_text().splitlines()) == 1


async def test_bad_shot_is_dropped_not_fatal_and_non_repo_refused(repo, tmp_path_factory):
    err, m = await call("marks_add", repo=str(repo), shot="data:image/png;base64,bm90IGEgcG5n", mark={"note": "x"})
    assert not err and "shot_path" not in m and len(m["id"]) == 12
    err, msg = await call("marks_list", repo=str(tmp_path_factory.mktemp("plain")))
    assert err and "not a git checkout" in msg


async def test_update_appends_history_and_list_filters(repo):
    await call("marks_add", repo=str(repo), mark={"id": "a", "note": "n"})
    await call("marks_add", repo=str(repo), mark={"id": "b", "note": "n"})
    err, m = await call("marks_update", repo=str(repo), id="a", status="fixed", pr="https://x/pr/1")
    assert m["status"] == "fixed" and m["pr"] == "https://x/pr/1"
    assert [h["status"] for h in m["history"]] == ["open", "fixed"]
    err, out = await call("marks_list", repo=str(repo), status=["open"])
    assert [x["id"] for x in out["marks"]] == ["b"]
    err, msg = await call("marks_update", repo=str(repo), id="a", status="gone")
    assert err


class _Res:
    def __init__(self, content, is_error=False):
        self.content, self.is_error = content, is_error


async def _run_fix(repo, self_dev_result, **args):
    calls, events = [], []

    async def call(name, a):
        calls.append((name, a))
        return self_dev_result

    async def bc(ev):
        events.append(ev)

    plugin = M.MarksPlugin(call_tool_fn=call, broadcast_fn=bc)
    res = await plugin.call_tool("marks_fix", {"repo": str(repo), **args})
    if not res.is_error:
        await plugin._task
    return res, calls, events


async def test_fix_merged_marks_fixed_with_pr_and_no_restart_for_other_repo(repo):
    await call("marks_add", repo=str(repo), mark={"id": "a", "note": "too big", "type": "fix", "locator": {"sel": "div.x", "text": "Hi"}})
    await call("marks_add", repo=str(repo), mark={"id": "b", "note": "gone", "type": "remove"})
    await call("marks_update", repo=str(repo), id="b", status="verified")
    ok = _Res(json.dumps({"merge_decision": "auto_merge", "pr_url": "https://x/pr/7"}))
    res, calls, events = await _run_fix(repo, ok, model="custom/budd")
    assert json.loads(res.content) == {"started": True, "marks": 1, "model": "custom/budd"}
    name, a = calls[0]
    assert name == "self_dev" and a["model"] == "custom/budd" and a["no_load"] is True
    assert a["target_dir"] == str(repo) and "too big" in a["change_description"] and "div.x" in a["change_description"]
    assert [c[0] for c in calls] == ["self_dev"]  # not Felix's own repo: no self_dev_load
    err, out = await call("marks_list", repo=str(repo))
    by = {m["id"]: m for m in out["marks"]}
    assert by["a"]["status"] == "fixed" and by["a"]["pr"] == "https://x/pr/7"
    assert [h["status"] for h in by["a"]["history"]] == ["open", "fixing", "fixed"]
    assert by["b"]["status"] == "verified"
    assert events[0]["type"] == "marks_changed"


async def test_fix_failed_tests_reopens_with_note_and_guards(repo):
    await call("marks_add", repo=str(repo), mark={"id": "a", "note": "n"})
    red = _Res(json.dumps({"merge_decision": "tests_failed", "pr_url": "https://x/pr/8"}))
    await _run_fix(repo, red)
    err, out = await call("marks_list", repo=str(repo))
    m = out["marks"][0]
    assert m["status"] == "reopened" and m["pr"] == "https://x/pr/8"
    assert "tests failed" in m["history"][-1]["note"]
    res, calls, _ = await _run_fix(repo, red, ids=["zzz"])
    assert res.is_error and calls == []


def test_statuses_match_the_schema():
    from pathlib import Path
    schema = json.loads((Path(M.__file__).parents[1] / "tools" / "mark-kit" / "mark.schema.json").read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["status"]["enum"]) == M.STATUSES
