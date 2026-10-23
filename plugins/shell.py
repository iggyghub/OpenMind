"""
Shell plugin -- MCP server for Felix.

Tools: run_command -> {stdout, stderr, exit_code[, exported]}.
Routes through WindowsSandbox (ADR-0010 SBX-3): never calls subprocess directly.

The sandbox can only see its own workdir, so file I/O crosses the boundary
here, in the unsandboxed plugin: ``input_files`` copies conversation
attachments in (only paths under the attachment store), ``output_files``
copies results out to the user's Downloads/Felix folder.
"""
import json
import shutil
import sys
from pathlib import Path
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "shell"

# ADR-0005 / Issue #44 -- run_command runs arbitrary user-supplied shell. This
# is the canonical shell_exec tool; the default policy denies it unless the
# user opts in via a one-time settings flag.
# fs_read: input_files reads attachments; fs_write: output_files writes to Downloads.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"shell_exec", "fs_write", "fs_read"})

# Wired by main.py via set_workdir_fn; returns the per-profile sandbox workdir.
_workdir_fn: Callable[[], str] | None = None

from cerebral.paths import data_dir

_DEFAULT_WORKDIR = str(data_dir() / "sandbox" / "default")
_ATTACHMENTS_ROOT = data_dir() / "attachments"  # == cerebral.db.attachments.DEFAULT_STORE_ROOT
_EXPORT_DIR = Path.home() / "Downloads" / "Felix"


def set_workdir_fn(fn: Callable[[], str]) -> None:
    global _workdir_fn
    _workdir_fn = fn


def _resolve_workdir() -> str:
    if _workdir_fn is not None:
        return _workdir_fn()
    wd = Path(_DEFAULT_WORKDIR)
    wd.mkdir(parents=True, exist_ok=True)
    return str(wd)


def _stage_input(path: str, workdir: Path) -> None:
    """Copy one attachment into <workdir>/in/. Only the attachment store is
    reachable -- the model can't use this to pull arbitrary files into the
    sandbox. copyfile (not copy2) so the copy inherits the workdir ACL."""
    src = Path(path).resolve()
    if not src.is_relative_to(_ATTACHMENTS_ROOT.resolve()) or not src.is_file():
        raise ValueError(f"input_files: not an attachment: {path}")
    dest = workdir / "in" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: staged inputs are never cleaned up; prune in/ if disk use bites.
    shutil.copyfile(src, dest)


def _export_output(name: str, workdir: Path) -> str:
    """Copy one workdir file to Downloads/Felix, never overwriting."""
    src = (workdir / name).resolve()
    if not src.is_relative_to(workdir.resolve()) or not src.is_file():
        raise ValueError(f"output_files: no such file in the sandbox folder: {name}")
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest = _EXPORT_DIR / src.name
    n = 1
    while dest.exists():
        dest = _EXPORT_DIR / f"{src.stem} ({n}){src.suffix}"
        n += 1
    shutil.copyfile(src, dest)
    return str(dest)


_UNSET = object()  # sentinel: "caller did not pass sandbox"


class ShellPlugin:
    name = PLUGIN_NAME

    def __init__(self, sandbox=_UNSET) -> None:
        if sandbox is _UNSET:
            if sys.platform == "win32":
                from cerebral.sandbox import WindowsSandbox
                self._sandbox = WindowsSandbox()
            else:
                self._sandbox = None  # non-Windows: gate keeps shell_exec denied
        else:
            self._sandbox = sandbox  # explicit injection (None forces no-sandbox path)

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="run_command",
                description=(
                    "Run a cmd.exe command in a sandboxed folder (no network, can't see "
                    "the rest of the disk) and return stdout, stderr, and exit code. "
                    "The folder persists between calls. To work on a file the user "
                    "attached, pass its stored path in input_files: it is copied to "
                    "in\\<name> first. To hand a result back, write it in the folder "
                    "and list it in output_files: it is copied to the user's "
                    "Downloads\\Felix folder and the paths come back as 'exported'. "
                    "Raise timeout for slow jobs such as ffmpeg video edits."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                        "timeout": {
                            "type": "number",
                            "description": "Max seconds to wait (default 30)",
                        },
                        "input_files": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Attachment stored paths to copy into in\\ before running",
                        },
                        "output_files": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Paths relative to the folder to copy to Downloads\\Felix after running",
                        },
                    },
                    "required": ["command"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "run_command":
            return self._run_command(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _run_command(self, args: dict) -> ToolResult:
        if self._sandbox is None:
            return ToolResult(content="shell_exec is not available on this platform", is_error=True)
        command = args["command"]
        timeout = float(args.get("timeout", 30))
        workdir = _resolve_workdir()
        try:
            for p in args.get("input_files") or []:
                _stage_input(p, Path(workdir))
        except (ValueError, OSError) as exc:
            return ToolResult(content=str(exc), is_error=True)
        try:
            from cerebral.sandbox._interface import SandboxResult
            result: SandboxResult = self._sandbox.spawn(
                ["cmd", "/c", command],
                workdir,
                timeout_s=timeout,
            )
            out = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
            }
            outputs = args.get("output_files") or []
            if outputs:
                out["exported"], errors = [], []
                for name in outputs:
                    try:
                        out["exported"].append(_export_output(name, Path(workdir)))
                    except (ValueError, OSError) as exc:
                        errors.append(str(exc))
                if errors:
                    out["export_errors"] = errors
            return ToolResult(content=json.dumps(out), is_error=bool(result.killed_reason))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


def create() -> ShellPlugin:
    return ShellPlugin()
