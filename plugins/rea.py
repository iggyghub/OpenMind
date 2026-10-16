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

Tools: rea_status, rea_analyze_javascript. Also exposes panel_spec() (ADR-0012),
so the workspace "+ Panel" opener lists it as the "REA" panel.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
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

# rea_find_apps: where installed Electron apps usually keep resources/app.asar.
# Depth is counted in directories below the root: Discord/app-1.0/resources.
_FIND_DEPTH = 3
_FIND_SKIP = {"node_modules", "temp", "cache", "code cache", "gpucache", ".git"}


def _default_app_roots() -> list[Path]:
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots += [Path(local) / "Programs", Path(local)]
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    return roots

# REA's engines.node is "^22.19 || ^24.11 || >=26".
_NODE_MIN = {22: 19, 24: 11}
_NODE_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")

# npm installs `rea` as a .cmd shim that runs through cmd.exe on Windows, so a
# target path must not carry cmd metacharacters or newlines.
_BAD_PATH_CHARS = re.compile(r'[&|<>^%"!\r\n]')

_ANALYSIS_TIMEOUT_S = 900

# Native "Browse..." picker (tkinter ships with Python). It runs in its own
# short-lived process so a GUI dialog never blocks or crashes Cerebral.
_PICK_TIMEOUT_S = 300
_PICK_CODE = (
    "import sys, tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "if sys.argv[1] == 'folder':\n"
    "    p = filedialog.askdirectory(title='Choose an app folder to analyse')\n"
    "else:\n"
    "    p = filedialog.askopenfilename(title='Choose an .asar file to analyse',\n"
    "        filetypes=[('Electron app archive', '*.asar'), ('All files', '*.*')])\n"
    "print(p or '')\n"
)

# panel_spec() runs every time the panel list is requested; the status probe
# shells out twice, so cache it briefly.
_STATUS_TTL_S = 60
_PANEL_RUN_LIMIT = 20


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

    def __init__(
        self,
        run_fn: Callable | None = None,
        run_dir: Path | None = None,
        app_roots: list | None = None,
    ) -> None:
        self._run_fn = run_fn or subprocess.run
        self._run_dir = Path(run_dir) if run_dir else _DEFAULT_RUN_DIR
        self._app_roots = [Path(r) for r in app_roots] if app_roots is not None else _default_app_roots()
        self._status_cache: tuple[float, dict] | None = None

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
                    "Statically analyse a JavaScript/Electron application with REA. Give "
                    "EITHER app (the name of an installed program, e.g. 'signal', 'discord' "
                    "-- resolved to its app.asar automatically) OR path (an extracted app "
                    "directory or .asar). Returns a compact summary, statistics and "
                    "limitations; the full result is saved to disk and its path is returned."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "app": {
                            "type": "string",
                            "description": "Name of an installed Electron app, e.g. 'signal'. Use this when the user names a program.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Absolute path to an app directory or .asar file.",
                        },
                    },
                },
            ),
            Tool(
                name="rea_find_apps",
                description=(
                    "List installed Electron apps (their resources/app.asar) so a program "
                    "can be analysed by name. Optional query filters by name."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Case-insensitive part of the app name, e.g. signal.",
                        },
                    },
                },
            ),
            Tool(
                name="rea_browse_analyze",
                description=(
                    "Open a native file/folder picker on this computer, then analyse the "
                    "chosen JavaScript/Electron app with REA (same as rea_analyze_javascript)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["folder", "file"],
                            "description": "folder = an extracted app directory; file = an .asar archive.",
                        },
                    },
                    "required": ["kind"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        # The work below blocks (subprocesses that run for minutes, tens of MB
        # of JSON): run it in a worker thread so Cerebral's event loop -- chat,
        # the tray connection, other tools -- keeps running meanwhile.
        return await asyncio.to_thread(self._call_tool_sync, tool_name, args)

    def _call_tool_sync(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        if tool_name == "rea_status":
            return self._status()
        if tool_name == "rea_analyze_javascript":
            if args.get("app") and not args.get("path"):
                return self._analyze_app(args.get("app"))
            return self._analyze_javascript(args.get("path"))
        if tool_name == "rea_find_apps":
            return self._find_apps(args.get("query"))
        if tool_name == "rea_browse_analyze":
            return self._browse_analyze(args.get("kind"))
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

    def _cached_status(self) -> dict:
        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < _STATUS_TTL_S:
            return self._status_cache[1]
        payload = json.loads(self._status().content)
        self._status_cache = (now, payload)
        return payload

    def _recent_runs(self) -> list[dict]:
        if not self._run_dir.is_dir():
            return []
        files = sorted(
            self._run_dir.glob("*.summary.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        runs: list[dict] = []
        for f in files[:_PANEL_RUN_LIMIT]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                runs.append(data)
        return runs

    def panel_spec(self, profile_id: "int | None" = None) -> dict:  # noqa: ARG002
        """Declarative REA panel (ADR-0012): readiness, an analyse form, history."""
        status = self._cached_status()
        widgets: list[dict] = [
            {
                "type": "detail",
                "id": "rea-status",
                "fields": [
                    {
                        "label": "Node",
                        "value": status.get("node") or "missing",
                        "hint": "REA needs Node ^22.19, ^24.11 or >=26",
                    },
                    {
                        "label": "REA",
                        "value": f"{status.get('rea') or 'missing'} (verified: {REA_PINNED_VERSION})",
                    },
                    {"label": "Ready", "value": "yes" if status.get("ready") else "no"},
                ],
            }
        ]
        problems = status.get("problems") or []
        if problems:
            widgets.append({"type": "list", "items": [{"title": p} for p in problems]})
        widgets.append({
            "type": "action",
            "id": "rea-analyze",
            "label": "Analyse JS/Electron app",
            "tool": "rea_analyze_javascript",
            "tool_args": {},
            "input_arg": "path",
            "input_placeholder": "absolute path to an app folder or .asar",
        })
        widgets.append({
            "type": "action",
            "id": "rea-browse-folder",
            "label": "Browse for an app folder...",
            "tool": "rea_browse_analyze",
            "tool_args": {"kind": "folder"},
        })
        widgets.append({
            "type": "action",
            "id": "rea-browse-file",
            "label": "Browse for an .asar file...",
            "tool": "rea_browse_analyze",
            "tool_args": {"kind": "file"},
        })
        runs = self._recent_runs()
        if runs:
            items = []
            for run in runs:
                stats = run.get("statistics") or {}
                parts = [str(run.get("ts") or "")[:19], str(run.get("confidence") or "")]
                if stats.get("relevant_files") is not None:
                    parts.append(f"{stats['relevant_files']} files")
                if stats.get("findings") is not None:
                    parts.append(f"{stats['findings']} findings")
                items.append({
                    "title": str(run.get("target") or run.get("run_id") or ""),
                    "subtitle": " \u00b7 ".join(p for p in parts if p),
                })
            widgets.append({
                "type": "group",
                "label": "Recent runs",
                "count": str(len(runs)),
                "open": True,
                "widgets": [{"type": "list", "items": items}],
            })
        return {"title": "REA", "widgets": widgets}

    def _find_apps(self, query) -> ToolResult:
        q = str(query or "").strip().lower()
        best: dict[str, tuple[float, Path]] = {}  # app name -> (mtime, asar)
        seen: set = set()  # roots overlap (LOCALAPPDATA contains Programs)
        for root in self._app_roots:
            if not root.is_dir():
                continue
            base_depth = len(root.parts)
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                depth = len(here.parts) - base_depth
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in _FIND_SKIP and depth < _FIND_DEPTH
                ]
                if "app.asar" in filenames and here.name.lower() == "resources":
                    asar = here / "app.asar"
                    if asar in seen:
                        continue
                    seen.add(asar)
                    rel = asar.relative_to(root).parts
                    name = rel[0]
                    try:
                        mtime = asar.stat().st_mtime
                    except OSError:
                        continue
                    if name not in best or mtime > best[name][0]:
                        best[name] = (mtime, asar)
        apps = []
        for name, (_mtime, asar) in sorted(best.items(), key=lambda kv: kv[0].lower()):
            if q and q not in name.lower():
                continue
            try:
                size_mb = round(asar.stat().st_size / 1048576, 1)
            except OSError:
                size_mb = None
            apps.append({"name": name, "path": str(asar), "size_mb": size_mb})
        return ToolResult(content=json.dumps({"apps": apps}))

    def _browse_analyze(self, kind) -> ToolResult:
        if kind not in ("folder", "file"):
            return ToolResult(content="'kind' must be 'folder' or 'file'", is_error=True)
        try:
            proc = self._exec([sys.executable, "-c", _PICK_CODE, kind], timeout=_PICK_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return ToolResult(content="file picker timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"file picker failed to start: {exc}", is_error=True)
        if proc.returncode != 0:
            tail = (proc.stderr or "")[-300:]
            return ToolResult(content=f"file picker failed: {tail}", is_error=True)
        chosen = (proc.stdout or "").strip()
        if not chosen:
            return ToolResult(content="No path chosen; nothing analysed.")
        return self._analyze_javascript(os.path.normpath(chosen))

    def _analyze_app(self, app) -> ToolResult:
        """Resolve an installed app's name to its app.asar, then analyse it."""
        if not isinstance(app, str) or not app.strip():
            return ToolResult(content="'app' or 'path' is required", is_error=True)
        found = json.loads(self._find_apps(app).content)["apps"]
        if len(found) == 1:
            return self._analyze_javascript(found[0]["path"])
        names = [a["name"] for a in json.loads(self._find_apps(None).content)["apps"]]
        if not found:
            return ToolResult(
                content=f"No installed Electron app matches '{app}'. Installed: {', '.join(names) or 'none found'}.",
                is_error=True,
            )
        return ToolResult(
            content=f"'{app}' matches several apps: {', '.join(a['name'] for a in found)}. Which one?",
            is_error=True,
        )

    def _analyze_javascript(self, path) -> ToolResult:
        if not isinstance(path, str) or not path.strip():
            return ToolResult(content="'app' or 'path' is required", is_error=True)
        path = path.strip()
        if path.startswith("-") or _BAD_PATH_CHARS.search(path):
            return ToolResult(content=f"invalid path: {path!r}", is_error=True)
        if not os.path.exists(path):
            return ToolResult(content=f"path does not exist: {path}", is_error=True)

        argv = ["rea", "analyze-javascript-application", path, "--json"]
        integrity_note = None
        for attempt in range(2):
            try:
                proc = self._exec(argv, timeout=_ANALYSIS_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                return ToolResult(content="rea analysis timed out", is_error=True)
            except Exception as exc:
                return ToolResult(content=f"rea failed to start: {exc}", is_error=True)
            if proc.returncode == 0:
                break
            # rea reports a failed analysis as a JSON error document on stdout;
            # stderr only carries progress lines.
            err = {}
            try:
                parsed = json.loads(proc.stdout or "")
                err = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                pass
            if attempt == 0 and err.get("code") == "artifact_integrity_mismatch":
                # Installers often re-sign unpacked native binaries after writing
                # the archive, so the declared hash no longer matches. Static
                # analysis of the user's own installed copy is still meaningful;
                # rea keeps the observed bytes marked untrusted.
                argv = argv + ["--integrity-policy", "record-and-continue"]
                integrity_note = str(err.get("message") or "declared ASAR integrity did not match")[:300]
                continue
            reason = err.get("message") or (proc.stderr or "")[-500:]
            return ToolResult(content=f"rea exited {proc.returncode}: {str(reason)[:600]}", is_error=True)

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
            "integrity": "mismatch recorded (analysed with record-and-continue)" if integrity_note else "ok",
        }
        (self._run_dir / f"{run_id}.summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        return ToolResult(content=json.dumps(summary))


def create(**kwargs) -> ReaPlugin:
    return ReaPlugin()
