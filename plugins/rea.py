"""
REA plugin -- Felix drives the installed `rea` CLI (morluto/rea, npm package
`rea-agents`) to statically analyse local targets. ADR-0037 amendment
(2026-10-13): REA itself is a headless plugin; its page is the panel_spec below.

REA is installed separately (Node >=22.19 / >=24.11 / >=26, then
`npm install --global rea-agents@6.4.0` via the package_manager plugin). This
plugin never installs anything and never runs `rea-agents setup`.

Output handling: `rea ... --json` writes the result to stdout and JSON progress
lines to stderr. A full result is tens of MB (a 36-file directory was ~30 MB),
so the full JSON is saved to cerebral/data/rea_runs/<run_id>.json and only a
compact summary (+ the saved path) goes back to the model and the panel.

Tools: rea_status, rea_analyze_javascript.
"""
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "rea"

# The REA release this plugin was written and verified against.
REA_PINNED_VERSION = "6.4.0"

# ADR-0005: reads the analysis target (fs_read) and writes the saved run under
# cerebral/data/rea_runs (fs_write). The 16-class vocabulary is unchanged.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})

_DEFAULT_RUN_DIR = Path(__file__).resolve().parent.parent / "cerebral" / "data" / "rea_runs"

# REA's engines.node is "^22.19 || ^24.11 || >=26".
_NODE_MIN = {22: 19, 24: 11}
_NODE_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")

# npm installs `rea` as a .cmd shim that runs through cmd.exe on Windows, so a
# target path must not carry cmd metacharacters or newlines.
_BAD_PATH_CHARS = re.compile(r'[&|<>^%"!\r\n]')

_ANALYSIS_TIMEOUT_S = 900


def _node_ok(version: str) -> bool:
    m = _NODE_RE.match(version.strip())
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    if major >= 26:
        return True
    return major in _NODE_MIN and minor >= _NODE_MIN[major]


class ReaPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None, run_dir: Path | None = None) -> None:
        self._run_fn = run_fn or subprocess.run
        self._run_dir = Path(run_dir) if run_dir else _DEFAULT_RUN_DIR

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="rea_status",
                description=(
                    "Check that REA (reverse-engineering CLI) is ready: Node version, "
                    "installed `rea` version, and what to fix if something is missing."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="rea_analyze_javascript",
                description=(
                    "Statically analyse a local JavaScript/Electron application "
                    "(extracted directory or .asar) with REA. Returns a compact "
                    "summary, statistics and limitations; the full result is saved "
                    "to disk and its path is returned."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to an app directory or .asar file.",
                        },
                    },
                    "required": ["path"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        if tool_name == "rea_status":
            return self._status()
        if tool_name == "rea_analyze_javascript":
            return self._analyze_javascript(args.get("path"))
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # -- helpers ---------------------------------------------------------

    def _exec(self, argv: list[str], timeout: int = 60):
        if self._run_fn is subprocess.run:
            # On Windows a bare 'rea'/'node' may be a .cmd shim that
            # CreateProcess will not find; resolve via PATH first.
            argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
        return self._run_fn(
            argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )

    def _version_of(self, argv: list[str]) -> str | None:
        try:
            proc = self._exec(argv)
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip() or None

    def _status(self) -> ToolResult:
        node = self._version_of(["node", "--version"])
        rea = self._version_of(["rea", "--version"])
        node_ok = bool(node) and _node_ok(node)
        problems: list[str] = []
        if not node:
            problems.append("node not found on PATH")
        elif not node_ok:
            problems.append(f"node {node} is too old (need ^22.19, ^24.11 or >=26)")
        if not rea:
            problems.append("`rea` not found on PATH")
        elif rea != REA_PINNED_VERSION:
            problems.append(f"rea {rea} installed; this plugin is verified against {REA_PINNED_VERSION}")
        payload = {
            "node": node,
            "node_ok": node_ok,
            "rea": rea,
            "rea_pinned": REA_PINNED_VERSION,
            "ready": node_ok and rea is not None,
            "problems": problems,
        }
        return ToolResult(content=json.dumps(payload))

    def _analyze_javascript(self, path) -> ToolResult:
        if not isinstance(path, str) or not path.strip():
            return ToolResult(content="'path' is required", is_error=True)
        path = path.strip()
        if path.startswith("-") or _BAD_PATH_CHARS.search(path):
            return ToolResult(content=f"invalid path: {path!r}", is_error=True)
        if not os.path.exists(path):
            return ToolResult(content=f"path does not exist: {path}", is_error=True)

        try:
            proc = self._exec(
                ["rea", "analyze-javascript-application", path, "--json"],
                timeout=_ANALYSIS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="rea analysis timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"rea failed to start: {exc}", is_error=True)
        if proc.returncode != 0:
            tail = (proc.stderr or "")[-500:]
            return ToolResult(content=f"rea exited {proc.returncode}: {tail}", is_error=True)

        try:
            doc = json.loads(proc.stdout)
        except (TypeError, ValueError):
            return ToolResult(content="rea returned non-JSON output", is_error=True)
        if not isinstance(doc, dict):
            return ToolResult(content="rea returned unexpected JSON", is_error=True)

        run_id = uuid.uuid4().hex[:12]
        normalized = doc.get("normalized_result") or {}
        subject = doc.get("subject") or {}
        self._run_dir.mkdir(parents=True, exist_ok=True)
        full_path = self._run_dir / f"{run_id}.json"
        full_path.write_text(proc.stdout, encoding="utf-8")
        summary = {
            "run_id": run_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "operation": doc.get("operation"),
            "target": subject.get("name") or path,
            "format": normalized.get("format"),
            "confidence": doc.get("confidence"),
            "authority": doc.get("authority"),
            "rea_version": self._version_of(["rea", "--version"]),
            "summary": normalized.get("summary"),
            "statistics": normalized.get("statistics"),
            "limitations": doc.get("limitations") or [],
            "full_result_path": str(full_path),
        }
        (self._run_dir / f"{run_id}.summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        return ToolResult(content=json.dumps(summary))


def create(**kwargs) -> ReaPlugin:
    return ReaPlugin()
