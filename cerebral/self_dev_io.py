"""Self-dev git/gh/pytest I/O -- lives OUTSIDE plugins/ on purpose.

`plugins/self_dev.py` is scanned by the ADR-0005 inspectability gate, which
forbids `subprocess.run(` in any inspected plugin body. The self-dev loop
genuinely has to shell out to git / gh / pytest, so those calls live here
(cerebral/ is not scanned) and the plugin imports them -- the same split
`plugins/shell.py` uses to route through `cerebral.sandbox`.

Kept as thin module-level functions so `plugins.self_dev` can re-alias them as
its `_default_*_fn` seams and the existing tests (which monkeypatch the global
`subprocess.run`) keep working unchanged.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Search/replace edit block: <<<FILE: path>>> <<<SEARCH>>> .. <<<REPLACE>>> .. <<<END>>>
# Small, escaping-free output a local model can emit reliably (whole-file JSON
# rewrite is beyond a 7-8B on this box).
_SR_BLOCK = re.compile(
    r"<<<FILE:\s*(.+?)>>>\s*<<<SEARCH>>>\r?\n(.*?)\r?\n<<<REPLACE>>>\r?\n(.*?)\r?\n?<<<END>>>",
    re.DOTALL,
)

# New-file block: <<<NEWFILE: path>>>\n<full body>\n<<<END>>>. Search/replace
# can only touch EXISTING files, so a proposal that needs a new module (the
# common case) could never commit -- the whole class failed at "no commit".
# `<<<NEWFILE:` never collides with `<<<FILE:` (different 4th char after `<<<`).
_NEWFILE_BLOCK = re.compile(
    r"<<<NEWFILE:\s*(.+?)>>>\r?\n(.*?)\r?\n?<<<END>>>",
    re.DOTALL,
)


def apply_search_replace(
    clone_dir: Path, text: str, allowed: "set[str] | None" = None
) -> "list[str]":
    """Apply search/replace blocks from a model reply to files under clone_dir.
    Exact match only; a miss is skipped (fail-safe -> no commit -> gate escalates).
    Returns the list of repo-relative paths actually changed.

    `allowed`, when given, restricts writes to that exact set of repo-relative
    paths (the file-planning step's own answer for "which files will this
    touch") -- issue #986: an edit-step reply that comes back wrong/unrelated
    (observed: a CSS-only task produced a 1000+-line diff across unrelated
    trading files, byte-identical across two independent runs -- root cause
    not pinned down, but not reproducible in this codebase's own request
    handling) would otherwise still get written and committed as long as its
    paths existed and its SEARCH anchors happened to match. This guard makes
    that class of reply inert instead of merely relying on the test gate to
    catch it downstream.
    # ponytail: exact match only; add whitespace-lenient matching if local
    # models miss the anchor too often."""
    clone_dir = Path(clone_dir)
    root = str(clone_dir.resolve())
    applied: list[str] = []
    if not text:
        # An intermittent model-server stall can return content=None from a
        # valid HTTP 200 (same failure class as extract_json_value) -- this
        # used to crash re.finditer outright ("expected string or
        # bytes-like object, got 'NoneType'") instead of failing soft into
        # the already-understood "no commit" path.
        return applied
    for m in _SR_BLOCK.finditer(text):
        rel, search, replace = m.group(1).strip(), m.group(2), m.group(3)
        if allowed is not None and rel not in allowed:
            continue  # not a file the planning step said it would touch
        fp = (clone_dir / rel).resolve()
        if not str(fp).startswith(root) or not fp.is_file() or not search:
            continue  # path-escape guard / missing file / empty anchor
        body = fp.read_text(encoding="utf-8")
        if search in body:
            fp.write_text(body.replace(search, replace, 1), encoding="utf-8")
            applied.append(rel)
    # New-file blocks: create-only. Same path-escape guard; refuse to clobber
    # an existing file (that path is search/replace's job) so a stray NEWFILE
    # can't blank a real source file.
    for m in _NEWFILE_BLOCK.finditer(text):
        rel, content = m.group(1).strip(), m.group(2)
        if allowed is not None and rel not in allowed:
            continue  # not a file the planning step said it would touch
        fp = (clone_dir / rel).resolve()
        if not str(fp).startswith(root) or fp.exists():
            continue  # path-escape guard / never overwrite an existing file
        # The \n before <<<END>>> is a delimiter, not body -- restore a single
        # trailing newline so the created source file is POSIX-clean.
        if content and not content.endswith("\n"):
            content += "\n"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        applied.append(rel)
    return applied


def extract_json_value(text: str, opener: str):
    """First JSON value of the given kind ('[' or '{') in a model reply,
    tolerating ```json fences and surrounding prose. Returns the parsed value,
    or None when nothing parseable is found (including a None/empty reply --
    an intermittent Budd stall can return content=None from a valid HTTP 200,
    which used to crash this function outright instead of failing soft)."""
    if not text:
        return None
    closer = "]" if opener == "[" else "}"
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None

# self_dev_io.py -> cerebral/ -> repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def git_config_get(repo_root: Path, key: str) -> str:
    """Read a git config value from a repo, or '' if unset/unavailable."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "config", key],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def clone_fn(repo_url: str, dest: Path) -> None:
    """Full local git clone -- live .git is never shared with the sandbox.

    `repo_url` is either Felix's own repo (the default) or an external
    target_dir (self_dev, generalized to point at any local repo, not just
    Felix's own -- issue #1059/#1060). A fresh clone inherits no committer
    identity (global config may be unset), so a later commit would die with
    'Author identity unknown'. Carry the SOURCE repo's identity/origin into
    the clone (falling back to a self-dev default) -- source_root is
    whichever repo was actually cloned, so this works the same way for
    both cases.
    """
    result = subprocess.run(
        ["git", "clone", repo_url, str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr.strip()}")

    source_root = Path(repo_url) if Path(repo_url).is_dir() else _REPO_ROOT
    name = git_config_get(source_root, "user.name") or "Felix self-dev"
    email = git_config_get(source_root, "user.email") or "felix-self-dev@localhost"
    for key, val in (("user.name", name), ("user.email", email)):
        subprocess.run(
            ["git", "-C", str(dest), "config", key, val],
            capture_output=True, text=True, check=True,
        )

    origin = subprocess.run(
        ["git", "-C", str(source_root), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    url = origin.stdout.strip()
    if origin.returncode == 0 and url and not url.startswith(str(source_root)):
        subprocess.run(
            ["git", "-C", str(dest), "remote", "set-url", "origin", url],
            capture_output=True, text=True, check=True,
        )


def create_branch_and_commit(clone_dir: Path, branch: str, message: str) -> bool:
    """Create `branch`, stage everything, commit. Returns True iff a commit
    was actually made (False when the working tree had no changes)."""
    subprocess.run(
        ["git", "-C", str(clone_dir), "checkout", "-b", branch],
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(clone_dir), "add", "-A"],
        capture_output=True, text=True, check=True,
    )
    result = subprocess.run(
        ["git", "-C", str(clone_dir), "commit", "-m", message],
        capture_output=True, text=True,
    )
    return result.returncode == 0


_TEST_TIMEOUT_S = 1200.0  # ample for a full suite (20m); bounds a hung/runaway test


def test_fn(clone_dir: Path) -> "tuple[bool, str]":
    """Run pytest in the clone (inside the sandbox via main.py wiring).

    Runs BOTH test roots: cerebral/tests/ AND the repo-root tests/. The gate
    used to run cerebral/tests/ only, so a self-dev change that added a broken
    test under tests/ (repo root) passed the gate and auto-merged red -- exactly
    how #723 landed a failing tests/test_router_usage.py on master. Any dir that
    is absent in the clone is simply skipped by pytest, so this stays safe when
    a repo has only one test root.

    Bounded by _TEST_TIMEOUT_S: a hung/runaway test (e.g. a fixture waiting on
    a real network call) used to block this subprocess.run forever, and since
    the caller (plugins/self_dev.py) invokes this synchronously inside an async
    handler, an unbounded hang froze the whole Cerebral event loop -- no WS, no
    heartbeats, nothing -- until someone force-killed the process. A timeout
    turns that into a failed slice instead of a dead app.
    """
    roots = [d for d in ("cerebral/tests/", "tests/") if (clone_dir / d).is_dir()]
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", *roots, "-q", "--tb=short"],
            capture_output=True, text=True, cwd=str(clone_dir),
            timeout=_TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        partial = ((exc.stdout or "") + (exc.stderr or "")).strip()
        return False, (
            f"test run timed out after {_TEST_TIMEOUT_S:.0f}s -- killed\n{partial}"
        ).strip()
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def pr_fn(
    clone_dir: Path,
    branch: str,
    description: str,
    test_passed: bool,
    test_output: str,
) -> str:
    """Push branch and open a PR via gh CLI."""
    # -u sets upstream tracking; without it, `gh pr create` refuses.
    subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=str(clone_dir), check=True, capture_output=True,
    )
    badge = "Tests: PASS" if test_passed else "Tests: FAIL"
    body = f"{description}\n\n{badge}\n\n```\n{test_output[:2000]}\n```"
    # GitHub caps PR titles at 256 chars; a long change_description used as-is
    # makes `gh pr create` fail. Use the first line, truncated; the full text
    # stays in the body.
    title = (description.strip().splitlines() or ["self-dev change"])[0][:256]
    # --head names the branch explicitly so gh does not depend on upstream
    # tracking refs (which a single-branch clone may not have).
    result = subprocess.run(
        ["gh", "pr", "create", "--head", branch,
         "--title", title, "--body", body],
        capture_output=True, text=True, cwd=str(clone_dir),
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr create failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def diff_fn(pr_url: str) -> "list[str]":
    """List changed files in a PR via gh CLI."""
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "files", "--jq", ".files[].path"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view files failed:\n{result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def merge_fn(pr_url: str) -> None:
    """Squash-merge a PR and delete its branch via gh CLI."""
    result = subprocess.run(
        ["gh", "pr", "merge", pr_url, "--squash", "--delete-branch"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr merge failed:\n{result.stderr.strip()}")


def pr_state_fn(pr_url: str) -> str:
    """Live PR state via gh CLI -- "OPEN", "MERGED", or "CLOSED".

    #810 -- used to keep the in-chat pending-review card honest when a PR
    is merged/closed directly on GitHub instead of via the card's button.
    """
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view state failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def pull_fn(repo_root: Path) -> "tuple[bool, str]":
    """git pull --ff-only master in the live repo root."""
    result = subprocess.run(
        ["git", "pull", "origin", "master", "--ff-only"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(f"git pull failed:\n{output}")
    updated = "already up to date" not in output.lower()
    return updated, output


def issue_fn(issue_number: int) -> str:
    """Fetch GitHub issue title + body via gh CLI.

    Returns: "# Title\n\nBody text"
    Tests always inject this seam -- never call real gh in tests.
    """
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "title,body"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh issue view {issue_number} failed:\n{result.stderr.strip()}"
        )
    data = json.loads(result.stdout)
    title = data.get("title") or f"Issue {issue_number}"
    body = data.get("body") or ""
    return f"# {title}\n\n{body}"
