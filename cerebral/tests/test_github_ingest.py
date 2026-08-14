"""github_ingest tracer-bullet tests -- ADR-0018 S3. Stubbed clone/head/fetch/
extract: no network, no git. Verifies a repo's docs become source_type='github'
rows clustered into the shared collections."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import plugins.video as video_mod
from cerebral.llm.router import ModelUnavailableError
from cerebral.video import github_source as gs
from cerebral.video.store import VideoStore
from plugins import github_ingest as gi_mod
from plugins.github_ingest import GithubIngestPlugin


def _store() -> VideoStore:
    return VideoStore(db_path=Path(":memory:"))


def _clone_stub(files: dict):
    def _clone(url, dest):
        d = Path(dest)
        d.mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    return _clone


_seen = {}


async def _fake_extract(transcript, ocr, vis, labels, category=""):
    _seen["transcript"] = transcript
    return {"idea": "an idea", "cluster_label": "Doc Ideas", "people_required": 1}


@pytest.fixture(autouse=True)
def _reset():
    _seen.clear()
    yield
    video_mod.set_store(None) if hasattr(video_mod, "set_store") else None
    video_mod.set_extract_fn(None)
    gs.set_clone_fn(None)
    gs.set_head_sha_fn(None)
    gs.set_fetch_page_fn(None)
    gi_mod.set_route_extraction_local_fn(None)


def _wire(store, files):
    video_mod.set_store(store)
    video_mod.set_extract_fn(_fake_extract)
    gs.set_clone_fn(_clone_stub(files))
    gs.set_head_sha_fn(lambda d: "sha123")
    gs.set_fetch_page_fn(
        lambda url: '<meta property="og:description" content="A tiny tool.">'
    )


async def test_github_ingest_clusters_docs_into_collection():
    store = _store()
    _wire(store, {
        "README.md": " ".join(["word"] * 300),
        "docs/guide.md": " ".join(["word"] * 300),
    })
    r = await GithubIngestPlugin().call_tool(
        "github_ingest",
        {"repo_url": "https://github.com/acme/harness", "category": "harness improvement"},
    )
    assert not r.is_error, r.content
    data = json.loads(r.content)
    assert data["docs"] == 2 and data["extracted"] == 2

    v = store.get_by_url("https://github.com/acme/harness#README.md")
    assert v.source_type == "github"
    assert v.stage == "verified"
    assert v.collection == "harness improvement"

    clusters = store.list_clusters("harness improvement", source_type="github")
    assert [c["label"] for c in clusters] == ["Doc Ideas"]

    repo = store.get_github_repo("https://github.com/acme/harness")
    assert repo["head_sha"] == "sha123"
    assert repo["description"] == "A tiny tool."

    # Grounding (repo name + description) was prepended to the extractor input.
    assert _seen["transcript"].startswith("Repo: harness -- A tiny tool.")


async def test_github_ingest_blank_category_defaults_uncategorised():
    store = _store()
    _wire(store, {"README.md": " ".join(["word"] * 300)})
    r = await GithubIngestPlugin().call_tool(
        "github_ingest", {"repo_url": "https://github.com/a/b"}
    )
    data = json.loads(r.content)
    assert data["collection"] == "Uncategorised"


async def test_github_ingest_idempotent_skips_unchanged():
    store = _store()
    _wire(store, {"README.md": " ".join(["word"] * 300)})
    plugin = GithubIngestPlugin()
    await plugin.call_tool("github_ingest", {"repo_url": "https://github.com/a/b"})
    r2 = await plugin.call_tool("github_ingest", {"repo_url": "https://github.com/a/b"})
    data = json.loads(r2.content)
    assert data["skipped"] == 1
    assert data["extracted"] == 0


async def test_github_ingest_requires_repo_url():
    store = _store()
    video_mod.set_store(store)
    r = await GithubIngestPlugin().call_tool("github_ingest", {})
    assert r.is_error


def test_normalize_repo_url():
    from plugins.github_ingest import _normalize_repo_url
    ok = "https://github.com/acme/harness"
    assert _normalize_repo_url("https://github.com/acme/harness") == ok
    assert _normalize_repo_url("https://github.com/acme/harness.git") == ok
    assert _normalize_repo_url("http://www.github.com/acme/harness/") == ok
    assert _normalize_repo_url("https://github.com/acme/harness/tree/main/docs") == ok
    # Non-repo URLs -> None (a blog article, a bare host, gists elsewhere).
    assert _normalize_repo_url("https://cursor.com/blog/continually-improving-agent-harness") is None
    assert _normalize_repo_url("https://github.com/acme") is None
    assert _normalize_repo_url("not a url") is None


async def test_github_ingest_rejects_non_repo_url():
    store = _store()
    video_mod.set_store(store)
    r = await GithubIngestPlugin().call_tool(
        "github_ingest", {"repo_url": "https://cursor.com/blog/some-post"}
    )
    assert r.is_error
    assert "Not a GitHub repo URL" in r.content


async def test_github_ingest_normalizes_tree_url():
    store = _store()
    _wire(store, {"README.md": " ".join(["word"] * 300)})
    r = await GithubIngestPlugin().call_tool(
        "github_ingest",
        {"repo_url": "https://github.com/acme/harness/tree/main", "category": "c"},
    )
    data = json.loads(r.content)
    assert data["repo"] == "https://github.com/acme/harness"   # normalized


async def test_github_ingest_multiple_urls():
    store = _store()
    _wire(store, {"README.md": " ".join(["word"] * 300)})
    r = await GithubIngestPlugin().call_tool("github_ingest", {
        "repo_url": "https://github.com/a/one, https://github.com/b/two\nhttps://cursor.com/blog/x",
        "category": "c",
    })
    data = json.loads(r.content)
    assert data["count"] == 3
    repos = {x["repo"]: x for x in data["repos"]}
    assert repos["https://github.com/a/one"]["extracted"] == 1
    assert repos["https://github.com/b/two"]["extracted"] == 1
    # The blog URL is rejected per-item without aborting the others.
    assert "error" in repos["https://cursor.com/blog/x"]


async def test_github_ingest_reports_already_ingested():
    store = _store()
    _wire(store, {"README.md": " ".join(["word"] * 300)})
    plugin = GithubIngestPlugin()
    r1 = json.loads((await plugin.call_tool(
        "github_ingest", {"repo_url": "https://github.com/a/b"})).content)
    assert r1["already_ingested"] is False
    r2 = json.loads((await plugin.call_tool(
        "github_ingest", {"repo_url": "https://github.com/a/b"})).content)
    assert r2["already_ingested"] is True
    assert r2["skipped"] == 1 and r2["extracted"] == 0


def _extract_failing_after(n_ok: int):
    """Async extract stub that succeeds n_ok times, then raises Budd-down."""
    calls = {"n": 0}

    async def _fx(transcript, ocr, vis, labels, category=""):
        calls["n"] += 1
        if calls["n"] > n_ok:
            raise ModelUnavailableError("model 'custom/budd' unavailable")
        return {"idea": "an idea", "cluster_label": "Doc Ideas", "people_required": 1}

    return _fx


async def test_github_ingest_requeues_repo_when_budd_down():
    # ADR-0019 S2: a Budd failure mid-repo abandons the rest and requeues the repo.
    store = _store()
    _wire(store, {
        "README.md": " ".join(["word"] * 300),
        "docs/guide.md": " ".join(["word"] * 300),
    })
    video_mod.set_extract_fn(_extract_failing_after(1))  # 1st doc ok, 2nd fails
    r = await GithubIngestPlugin().call_tool(
        "github_ingest", {"repo_url": "https://github.com/a/b", "category": "c"},
    )
    data = json.loads(r.content)
    assert data["requeued"] == "budd_unavailable"
    assert data["extracted"] == 1 and data["remaining"] == 1
    assert data["budd_requeues"] == 1
    assert store.get_budd_requeues("https://github.com/a/b") == 1


async def test_github_ingest_drains_to_local_after_three_requeues():
    # ADR-0019 S3: once a repo has 3 Budd requeues, ingest routes local + finishes.
    store = _store()
    _wire(store, {"README.md": " ".join(["word"] * 300)})
    url = "https://github.com/a/b"
    store.upsert_github_repo(url)
    for _ in range(3):
        store.bump_budd_requeues(url)

    route_calls = []
    gi_mod.set_route_extraction_local_fn(lambda local: route_calls.append(local))

    r = await GithubIngestPlugin().call_tool(
        "github_ingest", {"repo_url": url, "category": "c"},
    )
    data = json.loads(r.content)
    assert "requeued" not in data              # completed, not requeued
    assert data["extracted"] == 1
    assert route_calls == [True, False]        # drained local, then restored
    assert store.get_budd_requeues(url) == 0   # cleared on clean finish


async def test_github_ingest_clone_failure_errors():
    store = _store()
    video_mod.set_store(store)
    def boom(url, dest):
        raise RuntimeError("network down")
    gs.set_clone_fn(boom)
    r = await GithubIngestPlugin().call_tool(
        "github_ingest", {"repo_url": "https://github.com/a/b"}
    )
    assert r.is_error
    assert "Clone failed" in r.content
