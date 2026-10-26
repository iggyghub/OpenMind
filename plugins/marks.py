"""
Marks plugin -- the store behind mark mode (tools/mark-kit, MARK-MODE-IDEAS.md M4).

A mark is a UI change request made by pointing at a live app (schema:
tools/mark-kit/mark.schema.json). They live in the MARKED APP's own checkout:

    <repo>/.marks/marks.jsonl     one mark per line, rewritten on update
    <repo>/.marks/shots/<id>.png  the screenshot taken when it was marked
    <repo>/.marks/FIX.md          how a fixer works through them (copied in once)

`.marks/` is always gitignored (added to the repo's .gitignore if missing).
Marks are never deleted: the lifecycle is open -> fixing -> fixed -> verified |
reopened, or declined (with the fixer's reason); every change is appended to
the mark's `history`.

`repo` defaults to Felix's own checkout. Writes go through the ADR-0005 gate
like any tool (fs_write: ASK until the user allows it).
"""
import base64
import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "marks"
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIX_MD = _REPO_ROOT / "tools" / "mark-kit" / "FIX.md"
STATUSES = ("open", "fixing", "fixed", "verified", "reopened", "declined")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_SHOT_BYTES = 8 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo(arg) -> Path:
    repo = Path(arg) if arg else _REPO_ROOT
    if not (repo / ".git").exists():
        raise ValueError(f"not a git checkout: {repo}")
    return repo


def head_commit(repo: Path) -> str:
    """HEAD's sha read from .git directly (no git subprocess -- fs_read only).
    '' when it can't be worked out (detached oddities, packed-only refs missing)."""
    try:
        git = repo / ".git"
        if git.is_file():  # worktree: "gitdir: <path>"
            git = Path(git.read_text(encoding="utf-8").split(":", 1)[1].strip())
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        common = git
        if (git / "commondir").exists():
            common = (git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve()
        for base in (git, common):
            if (base / ref).exists():
                return (base / ref).read_text(encoding="utf-8").strip()
        packed = common / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
    except (OSError, IndexError):
        pass
    return ""


def _read(repo: Path) -> list[dict]:
    path = repo / ".marks" / "marks.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                logger.warning("[marks] skipping unreadable line in %s", path)
    return out


def _write(repo: Path, marks: list[dict]) -> None:
    path = repo / ".marks" / "marks.jsonl"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in marks), encoding="utf-8")
    tmp.replace(path)


def _prepare(repo: Path) -> None:
    """First-mark setup: .marks/shots, the .gitignore line, FIX.md."""
    (repo / ".marks" / "shots").mkdir(parents=True, exist_ok=True)
    ignore = repo / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if not any(l.strip() in (".marks", ".marks/", "/.marks", "/.marks/") for l in lines):
        with ignore.open("a", encoding="utf-8") as f:
            f.write(("\n" if lines and lines[-1] else "") + ".marks/\n")
    fix = repo / ".marks" / "FIX.md"
    if not fix.exists() and _FIX_MD.exists():
        shutil.copyfile(_FIX_MD, fix)


def _decode_png(shot: str) -> bytes:
    data = base64.b64decode(shot.split(",", 1)[1] if shot.startswith("data:") else shot, validate=True)
    if not data.startswith(_PNG_MAGIC):
        raise ValueError("shot is not a PNG")
    if len(data) > _MAX_SHOT_BYTES:
        raise ValueError("shot is larger than 8 MB")
    return data


def _with_abs(repo: Path, m: dict) -> dict:
    out = dict(m)
    if m.get("shot_path"):
        out["shot_abs"] = str(repo / m["shot_path"])
    return out


class MarksPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        repo_prop = {"type": "string", "description": "Absolute path of the marked app's git checkout. Omit for Felix's own."}
        return [
            Tool(
                name="marks_add",
                description=(
                    "Store a UI mark (mark mode / mark-kit) in <repo>/.marks/marks.jsonl, its "
                    "screenshot in .marks/shots/. Stamps id, status 'open' and the repo's HEAD commit. "
                    "Re-adding an existing id is a no-op that returns the stored mark."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "mark": {"type": "object", "description": "The mark (tools/mark-kit/mark.schema.json)."},
                        "shot": {"type": "string", "description": "PNG as a data URL or base64."},
                        "repo": repo_prop,
                    },
                    "required": ["mark"],
                },
            ),
            Tool(
                name="marks_list",
                description="List stored UI marks, optionally only those with the given status(es).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "array", "items": {"type": "string", "enum": list(STATUSES)}},
                        "repo": repo_prop,
                    },
                },
            ),
            Tool(
                name="marks_update",
                description=(
                    "Move a UI mark through its lifecycle (open, fixing, fixed, verified, reopened, "
                    "declined). Attach a note (why it was reopened), a reason (why it was declined) or "
                    "the fix's pr / commit. Marks are never deleted."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "status": {"type": "string", "enum": list(STATUSES)},
                        "note": {"type": "string"},
                        "reason": {"type": "string"},
                        "pr": {"type": "string"},
                        "commit": {"type": "string"},
                        "repo": repo_prop,
                    },
                    "required": ["id", "status"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        try:
            repo = _repo(args.get("repo"))
            if tool_name == "marks_add":
                return self._add(repo, args)
            if tool_name == "marks_list":
                return self._list(repo, args)
            if tool_name == "marks_update":
                return self._update(repo, args)
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _add(self, repo: Path, args: dict) -> ToolResult:
        mark = args.get("mark")
        if not isinstance(mark, dict):
            return ToolResult(content="mark must be an object", is_error=True)
        mark = {k: v for k, v in mark.items() if k not in ("shot", "shot_abs")}
        mid = str(mark.get("id") or "")
        if not _ID_RE.match(mid):
            mid = uuid.uuid4().hex[:12]
        marks = _read(repo)
        for m in marks:
            if m.get("id") == mid:
                return ToolResult(content=json.dumps(_with_abs(repo, m)))
        _prepare(repo)
        shot = args.get("shot")
        if shot:
            try:
                (repo / ".marks" / "shots" / f"{mid}.png").write_bytes(_decode_png(shot))
                mark["shot_path"] = f".marks/shots/{mid}.png"
            except (ValueError, base64.binascii.Error) as exc:
                logger.warning("[marks] screenshot for %s dropped: %s", mid, exc)
        now = _now()
        mark.update({"id": mid, "status": "open", "commit": head_commit(repo), "history": [{"status": "open", "at": now}]})
        mark.setdefault("created", now)
        marks.append(mark)
        _write(repo, marks)
        return ToolResult(content=json.dumps(_with_abs(repo, mark)))

    def _list(self, repo: Path, args: dict) -> ToolResult:
        want = args.get("status")
        if isinstance(want, str):
            want = [want]
        marks = [m for m in _read(repo) if not want or m.get("status") in want]
        return ToolResult(content=json.dumps({"marks": [_with_abs(repo, m) for m in marks]}))

    def _update(self, repo: Path, args: dict) -> ToolResult:
        status = args.get("status")
        if status not in STATUSES:
            return ToolResult(content=f"status must be one of {', '.join(STATUSES)}", is_error=True)
        marks = _read(repo)
        for m in marks:
            if m.get("id") == args.get("id"):
                entry = {"status": status, "at": _now()}
                for k in ("note", "reason", "pr", "commit"):
                    if args.get(k):
                        entry[k] = str(args[k])
                        if k in ("reason", "pr"):
                            m[k] = entry[k]
                        if k == "commit":
                            m["fix_commit"] = entry[k]
                m["status"] = status
                m.setdefault("history", []).append(entry)
                _write(repo, marks)
                return ToolResult(content=json.dumps(_with_abs(repo, m)))
        return ToolResult(content=f"no mark with id {args.get('id')!r}", is_error=True)


def create(**kwargs) -> MarksPlugin:
    return MarksPlugin()
