"""GitHub ingest MCP plugin -- ADR-0018 S3 (the tracer bullet).

github_ingest(repo_url, category): clone a repo (no API), read its curated
markdown docs, and extract one idea per doc into the SHARED clusters/collections
spine (channel.extract_and_cluster) -- so GitHub-sourced ideas sit alongside
video ones. Each doc becomes a source item row (source_type='github',
channel=repo_url) in the same videos table.

Acquisition seams live in cerebral.video.github_source (clone/head_sha/fetch),
all injectable so tests need no network or git. The store + extraction seam are
the video plugin's -- reused, not forked.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

import plugins.video as _video  # reuse the shared VideoStore singleton
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import channel as _channel
from cerebral.video import github_source as _gh

logger = logging.getLogger(__name__)

PLUGIN_NAME = "github_ingest"

# Same posture as the video plugin: clone is external_data_read + fs_write.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"external_data_read", "fs_write"})


def _repo_name(repo_url: str) -> str:
    return repo_url.rstrip("/").split("/")[-1]


# A GitHub repo URL: github.com/<owner>/<repo>, tolerating .git and deeper paths
# (tree/blob/...). Non-github hosts (a blog article, say) are rejected up front so
# the user gets a clear message instead of git's cryptic "exit 128".
_GH_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#]+?)(?:\.git)?(?:[/#].*)?$",
    re.I,
)


def _normalize_repo_url(url: str) -> "str | None":
    """Canonical https://github.com/owner/repo, or None if it isn't a repo URL."""
    m = _GH_URL.match((url or "").strip())
    if not m:
        return None
    return f"https://github.com/{m.group('owner')}/{m.group('repo')}"


def _grounding(repo_url: str, meta: dict) -> str:
    """One-line repo context prepended to each doc before extraction (ADR-0018 4)."""
    desc = (meta.get("description") or "").strip()
    topics = meta.get("topics") or []
    parts = [f"Repo: {_repo_name(repo_url)}"]
    if desc:
        parts.append(f"-- {desc}")
    if topics:
        parts.append(f"(topics: {', '.join(topics)})")
    return " ".join(parts)


def _split_urls(raw: str) -> list[str]:
    """One or many repo URLs from a field -- split on whitespace/commas/newlines."""
    return [u for u in re.split(r"[\s,]+", (raw or "").strip()) if u]


async def _ingest_repo(store, repo_url: str, category: str) -> dict:
    """Clone -> enumerate curated docs -> extract each into the shared clusters.

    Shared by github_ingest and github_reingest. Idempotent: unchanged +
    already-clustered docs are skipped. All acquisition is seam-injected.
    Returns a result dict; a clone failure yields {"repo", "error"}.
    """
    already = store.get_github_repo(repo_url) is not None
    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "repo"
        try:
            _gh.get_clone_fn()(repo_url, clone_dir)
        except Exception as exc:  # noqa: BLE001
            logger.error("[github] clone failed for %s: %s", repo_url, exc)
            return {"repo": repo_url, "error": f"Clone failed: {exc}"}

        head_sha = None
        try:
            head_sha = _gh.get_head_sha_fn()(clone_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[github] head_sha failed for %s: %s", repo_url, exc)

        try:
            meta = _gh.fetch_description(repo_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[github] description fetch failed for %s: %s", repo_url, exc)
            meta = {"description": "", "topics": []}

        docs = _gh.enumerate_docs(clone_dir)

    store.upsert_github_repo(
        repo_url, head_sha=head_sha, description=(meta.get("description") or None)
    )

    grounding = _grounding(repo_url, meta)
    results = []
    skipped = 0
    for d in docs:
        doc_url = repo_url + "#" + d["relpath"]
        existing = store.get_by_url(doc_url)
        if (
            existing is not None
            and (existing.transcript or "") == d["text"]
            and existing.stage in ("extracted", "verified")
        ):
            skipped += 1
            continue  # unchanged + already clustered -> idempotent skip
        vid = store.upsert(
            doc_url, channel=repo_url, collection=category, title=d["relpath"],
            transcript=d["text"], stage="transcribed", source_type="github",
        )
        # Grounding is prepended for extraction only; the row stores raw text so
        # the dedup content-compare above stays stable.
        text = (grounding + "\n\n" + d["text"]) if grounding else d["text"]
        final = await _channel.extract_and_cluster(
            store, video_id=vid, url=doc_url, transcript=text,
            collection=category, verify=False,
        )
        results.append({"doc": d["relpath"], "stage": final})

    return {
        "repo": repo_url,
        "collection": category,
        "already_ingested": already,
        "docs": len(docs),
        "extracted": len(results),
        "skipped": skipped,
        "results": results,
    }


class GithubIngestPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="github_ingest",
                description=(
                    "Read a GitHub repository's docs and extract an idea from each "
                    "markdown doc into a collection (category), filed alongside "
                    "video-sourced ideas. No API key -- clones the repo (git) and "
                    "reads the curated docs (README + docs/**). Blank category -> "
                    "'Uncategorised'; re-file later with video_move_cluster. "
                    "Idempotent: unchanged docs are skipped on re-ingest."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": (
                                "One or more GitHub repo URLs (space/comma/newline "
                                "separated), e.g. https://github.com/owner/repo"
                            ),
                        },
                        "category": {
                            "type": "string",
                            "description": (
                                "Collection to file the extracted ideas under "
                                "(e.g. 'harness improvement'). Blank -> 'Uncategorised'."
                            ),
                        },
                    },
                    "required": ["repo_url"],
                },
            ),
            Tool(
                name="github_reingest",
                description=(
                    "Re-read a repo you already ingested and extract any new or "
                    "changed docs (clears its 'update available' arrow). Uses the "
                    "collection the repo was filed under."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "repo_url": {"type": "string", "description": "Repo to re-ingest."},
                    },
                    "required": ["repo_url"],
                },
            ),
            Tool(
                name="github_check_updates",
                description=(
                    "Check every ingested repo for a newer remote HEAD (git "
                    "ls-remote, no API, no clone) and flag which have updates "
                    "available. Cheap; drives the GitHub panel's up-arrows."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset({"external_data_read"}),
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "github_ingest":
            return await self._github_ingest(args)
        if tool_name == "github_reingest":
            return await self._github_reingest(args)
        if tool_name == "github_check_updates":
            return self._github_check_updates(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    def panel_spec(self, profile_id: "int | None" = None) -> dict:  # noqa: ARG002
        """Declarative GitHub panel (ADR-0018 S5, ADR-0012).

        A repo ingest form, the ingested repos, and the source-scoped clusters --
        the last reuses the same cluster/group/move/commit widgets as the Videos
        panel (move_tool / commit are source-agnostic), and shows the SHARED
        clusters that contain >=1 github-sourced idea.
        """
        store = _video._get_store()
        widgets: list[dict] = [{
            "type": "action",
            "id": "github-ingest",
            "label": "Ingest repo",
            "tool": "github_ingest",
            "tool_args": {},
            "input_arg": "repo_url",
            "input_placeholder": "repo URL(s), space or comma separated",
            "input_arg2": "category",
            "input_placeholder2": "collection (blank = Uncategorised)",
        }]

        repos = store.list_github_repos()
        if repos:
            items = [{
                "title": _repo_name(r["repo_url"]),
                "subtitle": (r.get("description") or r["repo_url"]),
            } for r in repos]
            repo_children: list[dict] = [{"type": "list", "items": items}]
            # ADR-0018 S6: an up-arrow re-ingest action per repo whose remote HEAD
            # moved (set by github_check_updates). Absent when up-to-date.
            for r in repos:
                if r.get("update_available"):
                    name = _repo_name(r["repo_url"])
                    repo_children.append({
                        "type": "action",
                        "id": f"github-reingest-{name}",
                        "label": f"↑ {name}: update available",
                        "tool": "github_reingest",
                        "tool_args": {"repo_url": r["repo_url"]},
                    })
            widgets.append({
                "type": "group",
                "label": "Repositories",
                "count": str(len(repos)),
                "open": True,
                "widgets": repo_children,
            })

        clusters = store.list_clusters(source_type="github")
        if clusters:
            all_collections = sorted(store.list_collections())
            by_collection: dict[str, list[dict]] = {}
            for c in clusters:
                by_collection.setdefault(c.get("collection") or "Uncategorised", []).append(c)
            ordered = sorted(by_collection.items(), key=lambda kv: len(kv[1]), reverse=True)
            for gi, (collection, members) in enumerate(ordered):
                children: list[dict] = []
                for c in members:
                    n = c["member_count"]
                    parts = [f"{n} doc{'s' if n != 1 else ''}", _video.verdict_label(c["verdict"])]
                    if c["confidence"] is not None:
                        parts.append(f"{c['confidence']:.0%}")
                    if c.get("memory_id"):
                        parts.append("✓ Memory")
                    children.append({
                        "type": "cluster",
                        "cluster_id": c["id"],
                        "label": c["label"],
                        "stats": " · ".join(parts),
                        "collection": collection,
                        "collections": all_collections,
                        "move_tool": "video_move_cluster",
                    })
                    if c["verdict"] and not c.get("memory_id"):
                        children.append({
                            "type": "action",
                            "id": f"github-commit-{c['id']}",
                            "label": f"Commit {c['label']} to Memory",
                            "tool": "video_commit",
                            "tool_args": {"cluster_id": c["id"]},
                        })
                label = collection[:1].upper() + collection[1:]
                widgets.append({
                    "type": "group",
                    "label": label,
                    "collection": collection,
                    "count": f"{len(members)} cluster{'s' if len(members) != 1 else ''}",
                    "open": gi == 0,
                    "widgets": children,
                })
        else:
            widgets.append({"type": "list", "items": []})

        return {"title": "GitHub", "widgets": widgets}

    async def _github_ingest(self, args: dict) -> ToolResult:
        raw: str = (args.get("repo_url") or "").strip()
        category: str = (args.get("category") or "").strip() or "Uncategorised"
        if not raw:
            return ToolResult(content="repo_url is required", is_error=True)
        urls = _split_urls(raw)
        store = _video._get_store()
        out: list[dict] = []
        for u in urls:
            norm = _normalize_repo_url(u)
            if norm is None:
                out.append({"repo": u, "error": "Not a GitHub repo URL (expected github.com/owner/repo)"})
                continue
            out.append(await _ingest_repo(store, norm, category))
        # Single URL -> flat result (back-compat); many -> a list.
        if len(out) == 1:
            r = out[0]
            return ToolResult(content=json.dumps(r), is_error=("error" in r))
        return ToolResult(content=json.dumps({"repos": out, "count": len(out)}))

    async def _github_reingest(self, args: dict) -> ToolResult:
        """Re-ingest a repo (up-arrow click): re-clone, extract changed docs, clear
        the update flag. Category = whatever the repo's docs were filed under."""
        repo_url = _normalize_repo_url((args.get("repo_url") or "").strip())
        if repo_url is None:
            return ToolResult(content="repo_url must be a github.com/owner/repo URL", is_error=True)
        store = _video._get_store()
        category = store.get_repo_collection(repo_url) or "Uncategorised"
        result = await _ingest_repo(store, repo_url, category)
        if "error" not in result:
            store.set_update_available(repo_url, False)
        return ToolResult(content=json.dumps(result), is_error=("error" in result))

    def _github_check_updates(self, args: dict) -> ToolResult:  # noqa: ARG002
        """For each ingested repo, compare the remote HEAD (git ls-remote, no
        clone/API) to the stored SHA and flag update_available. Drives the arrow."""
        store = _video._get_store()
        flagged = 0
        for repo in store.list_github_repos():
            url = repo["repo_url"]
            try:
                remote = _gh.get_ls_remote_fn()(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[github] ls-remote failed for %s: %s", url, exc)
                continue
            avail = bool(remote) and remote != (repo.get("head_sha") or "")
            store.set_update_available(url, avail)
            if avail:
                flagged += 1
        return ToolResult(content=json.dumps({"checked": True, "updates_available": flagged}))


def create() -> GithubIngestPlugin:
    return GithubIngestPlugin()
