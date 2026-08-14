"""Up-arrow re-check tests -- ADR-0018 S6. Stubbed ls-remote/clone/extract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import plugins.video as video_mod
from cerebral.video import github_source as gs
from cerebral.video.store import VideoStore
from plugins.github_ingest import GithubIngestPlugin


def _store() -> VideoStore:
    s = VideoStore(db_path=Path(":memory:"))
    video_mod.set_store(s)
    return s


@pytest.fixture(autouse=True)
def _reset():
    yield
    video_mod.set_extract_fn(None)
    for setter in (gs.set_clone_fn, gs.set_head_sha_fn, gs.set_fetch_page_fn, gs.set_ls_remote_fn):
        setter(None)


# ── store: update_available flag ──────────────────────────────────────────────

def test_update_available_flag_roundtrip():
    s = _store()
    s.upsert_github_repo("https://gh/r", head_sha="abc")
    assert s.get_github_repo("https://gh/r")["update_available"] is False
    s.set_update_available("https://gh/r", True)
    assert s.get_github_repo("https://gh/r")["update_available"] is True
    assert s.list_github_repos()[0]["update_available"] is True


# ── github_check_updates: ls-remote vs stored SHA ─────────────────────────────

async def test_check_updates_flags_when_remote_moved():
    s = _store()
    s.upsert_github_repo("https://gh/moved", head_sha="old")
    s.upsert_github_repo("https://gh/same", head_sha="cur")
    gs.set_ls_remote_fn(lambda url: "new" if "moved" in url else "cur")
    r = await GithubIngestPlugin().call_tool("github_check_updates", {})
    assert json.loads(r.content)["updates_available"] == 1
    assert s.get_github_repo("https://gh/moved")["update_available"] is True
    assert s.get_github_repo("https://gh/same")["update_available"] is False


async def test_check_updates_clears_flag_when_caught_up():
    s = _store()
    s.upsert_github_repo("https://gh/r", head_sha="cur")
    s.set_update_available("https://gh/r", True)   # stale flag
    gs.set_ls_remote_fn(lambda url: "cur")          # remote now matches
    await GithubIngestPlugin().call_tool("github_check_updates", {})
    assert s.get_github_repo("https://gh/r")["update_available"] is False


# ── github_reingest: re-extract + clear flag ──────────────────────────────────

async def _fake_extract(t, o, v, clusters, category=""):
    return {"idea": "idea", "cluster_label": "L", "people_required": 1}


async def test_reingest_uses_repo_collection_and_clears_flag():
    s = _store()
    video_mod.set_extract_fn(_fake_extract)
    # First ingest under a known category.
    gs.set_clone_fn(lambda url, dest: (Path(dest).mkdir(parents=True, exist_ok=True),
                                       (Path(dest) / "README.md").write_text(
                                           " ".join(["word"] * 300), encoding="utf-8")))
    gs.set_head_sha_fn(lambda d: "sha1")
    gs.set_fetch_page_fn(lambda url: "")
    plugin = GithubIngestPlugin()
    await plugin.call_tool("github_ingest", {"repo_url": "https://gh/r", "category": "harness improvement"})
    s.set_update_available("https://gh/r", True)

    # Re-ingest: no category passed -> inferred from the repo's docs.
    r = await plugin.call_tool("github_reingest", {"repo_url": "https://gh/r"})
    data = json.loads(r.content)
    assert data["collection"] == "harness improvement"
    assert s.get_github_repo("https://gh/r")["update_available"] is False


# ── panel: up-arrow appears only when flagged ─────────────────────────────────

def test_panel_up_arrow_only_when_update_available():
    s = _store()
    s.upsert_github_repo("https://github.com/acme/tool", head_sha="x", description="d")
    spec = GithubIngestPlugin().panel_spec(None)
    assert not any(w.get("tool") == "github_reingest"
                   for g in spec["widgets"] if g.get("type") == "group"
                   for w in g.get("widgets", []))
    s.set_update_available("https://github.com/acme/tool", True)
    spec = GithubIngestPlugin().panel_spec(None)
    actions = [w for g in spec["widgets"] if g.get("type") == "group"
               for w in g.get("widgets", []) if w.get("tool") == "github_reingest"]
    assert len(actions) == 1
    assert actions[0]["tool_args"] == {"repo_url": "https://github.com/acme/tool"}
