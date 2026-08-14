"""GitHub panel_spec tests -- ADR-0018 S5. The panel shares the cluster/group/
move widgets with Videos and shows source-scoped (github) clusters + repos."""
from __future__ import annotations

from pathlib import Path

import plugins.video as video_mod
from cerebral.video.store import VideoStore
from plugins.github_ingest import GithubIngestPlugin


def _wire() -> VideoStore:
    store = VideoStore(db_path=Path(":memory:"))
    video_mod.set_store(store)
    return store


def _all_widgets(spec):
    for w in spec["widgets"]:
        yield w
        if w.get("type") == "group":
            yield from w.get("widgets", [])


def test_panel_spec_has_ingest_form_and_title():
    _wire()
    spec = GithubIngestPlugin().panel_spec(None)
    assert spec["title"] == "GitHub"
    form = next(w for w in spec["widgets"] if w.get("id") == "github-ingest")
    assert form["tool"] == "github_ingest"
    assert form["input_arg"] == "repo_url"
    assert form["input_arg2"] == "category"


def test_panel_spec_lists_repos_with_description_subtitle():
    store = _wire()
    store.upsert_github_repo("https://github.com/acme/harness", head_sha="s", description="A tool")
    spec = GithubIngestPlugin().panel_spec(None)
    repos_group = next(w for w in spec["widgets"] if w.get("label") == "Repositories")
    items = repos_group["widgets"][0]["items"]
    assert items[0]["title"] == "harness"
    assert items[0]["subtitle"] == "A tool"


def test_panel_spec_shows_only_github_clusters():
    store = _wire()
    # A github-sourced cluster and a video-sourced one in the same collection.
    g = store.upsert("https://gh/r#a.md", collection="harness improvement",
                     stage="verified", source_type="github")
    gc = store.get_or_create_cluster("Doc Idea", "harness improvement")
    store.upsert_idea(g, "gh idea", gc)
    v = store.upsert("https://yt/v", collection="harness improvement",
                     stage="verified", source_type="video")
    vc = store.get_or_create_cluster("Video Idea", "harness improvement")
    store.upsert_idea(v, "vid idea", vc)

    spec = GithubIngestPlugin().panel_spec(None)
    clusters = [w for w in _all_widgets(spec) if w.get("type") == "cluster"]
    labels = {c["label"] for c in clusters}
    assert labels == {"Doc Idea"}                    # video-only cluster not shown
    dc = next(c for c in clusters if c["label"] == "Doc Idea")
    assert dc["move_tool"] == "video_move_cluster"   # reuses the shared move tool
    # move targets include ALL collections (so a github cluster can move anywhere)
    assert "harness improvement" in dc["collections"]
    assert "doc" in dc["stats"]                       # github stat wording (docs, not videos)


def test_panel_spec_empty_when_no_github_clusters():
    _wire()
    spec = GithubIngestPlugin().panel_spec(None)
    # Still renders the ingest form + an empty list, no cluster widgets.
    assert not [w for w in _all_widgets(spec) if w.get("type") == "cluster"]
