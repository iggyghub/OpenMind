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
