"""
Video project MCP plugin -- ADR-0041, driver VIDEO-EDIT.md.

Tools: video_status (V1), video_project_create (V2).

V1: find_shotcut() / find_melt() discovery + video_status. Shotcut ships melt.exe
next to shotcut.exe, so one install is both the editor the user opens and the
headless engine Felix renders with.

V2: video_project_create -- the model hands over an edit list (clip path, start,
end); this plugin probes each clip with Shotcut's bundled ffprobe, serialises the
.mlt with cerebral/video/mlt.py (never model-written XML, ADR-0041 decision 4),
files it in the Document library under Videos/<YYYY>/<MM-DD>/ by the clips'
capture date, and writes a fingerprint beside it for V6's edit-or-fork rule.
Source clips are only referenced by path, never copied or modified.

Shotcut discovery is injectable via set_find_shotcut_fn for tests; clip probing
via set_probe_fn.
Never invoke a real shotcut.exe or melt.exe from within this module at import time or in tests.
"""
import asyncio
import hashlib
import json
import shutil
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.plugin_shared import state as _shared
from cerebral.video.mlt import ClipInfo, Cut, build_mlt

PLUGIN_NAME = "video_project"

# ADR-0005: video_status / probing read local files (fs_read);
# video_project_create writes the .mlt into the Document library (fs_write).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})

_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"})

# winget's default scope is per-user (%LOCALAPPDATA%/Programs); melt runs unsandboxed
# (ADR-0041 decision 3), so a per-user install works as well as a machine-wide one.
_SHOTCUT_DEFAULT_DIRS: tuple[Path, ...] = (
    Path("C:/Program Files/Shotcut"),
    Path("C:/Program Files (x86)/Shotcut"),
    Path.home() / "AppData/Local/Programs/Shotcut",
)

# Injectable seam: tests replace this to avoid real filesystem/process calls.
_find_shotcut_fn = None


def set_find_shotcut_fn(fn) -> None:
    global _find_shotcut_fn
    _find_shotcut_fn = fn


def find_shotcut(_dirs=None) -> "Path | None":
    """Locate shotcut.exe in standard install dirs then PATH."""
    dirs = _dirs if _dirs is not None else _SHOTCUT_DEFAULT_DIRS
    for d in dirs:
        p = d / "shotcut.exe"
        if p.exists():
            return p
    found = shutil.which("shotcut")
    return Path(found) if found else None


def find_melt(shotcut: Path) -> "Path | None":
    """melt.exe ships in the same folder as shotcut.exe."""
    p = shotcut.parent / "melt.exe"
    return p if p.exists() else None


def find_ffprobe(shotcut: Path) -> "Path | None":
    """Shotcut also bundles ffprobe.exe, used to read a clip's size, rate and length."""
    p = shotcut.parent / "ffprobe.exe"
    return p if p.exists() else None


_PROBE_TIMEOUT = 30.0  # seconds


async def probe_clip(path: Path, ffprobe: Path) -> ClipInfo:
    """Read width/height/frame rate/duration/audio presence with ffprobe.
    asyncio.create_subprocess_exec (argv, no shell), as documents.py runs LibreOffice:
    the inspectability scan refuses plugins that use the subprocess module."""
    proc = await asyncio.create_subprocess_exec(
        str(ffprobe), "-v", "error", "-show_entries",
        "stream=codec_type,width,height,r_frame_rate:format=duration",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise ValueError(f"ffprobe timed out reading {path.name}")
    if proc.returncode != 0:
        raise ValueError(f"ffprobe could not read {path.name}: {err.decode(errors='replace').strip()[:200]}")
    data = json.loads(out)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"no video stream: {path.name}")
    return ClipInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        fps=Fraction(video["r_frame_rate"]),
        duration=float(data["format"]["duration"]),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


# Injectable seam: async callable(path: Path) -> ClipInfo.
_probe_fn = None


def set_probe_fn(fn) -> None:
    global _probe_fn
    _probe_fn = fn


def capture_date(paths: list[Path]) -> date:
    """The day the clips were captured: the earliest file modification time.
    Game Bar writes each clip when the hotkey is pressed, so mtime is the moment."""
    return min(datetime.fromtimestamp(p.stat().st_mtime) for p in paths).date()


def project_folder(day: date) -> str:
    return f"Videos/{day:%Y}/{day:%m-%d}"


def unique_name(taken: set, base: str) -> str:
    """'Reel 2026-10-23', then 'Reel 2026-10-23 (2)', ' (3)', ..."""
    if base not in taken:
        return base
    n = 2
    while f"{base} ({n})" in taken:
        n += 1
    return f"{base} ({n})"


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_INSTALL_MSG = (
    "Shotcut not found. Run scripts/setup-shotcut.ps1 to install it "
    "(it also provides melt, the engine Felix renders video projects with)."
)


class VideoProjectPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list:
        return [
            Tool(
                name="video_status",
                description=(
                    "Returns whether Shotcut (the video editor) and its melt render "
                    "engine are installed. available=true when both are found; "
                    "false with an install hint."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="video_project_create",
                description=(
                    "Make a new video project (a Shotcut timeline, .mlt) from an edit "
                    "list of clips in order, each with start and end in seconds from "
                    "that clip's beginning. Clips stay where they are; only the "
                    "timeline is saved, in Library > Documents under Videos/<year>/"
                    "<month-day> of the day the clips were captured. Does not render."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "clips": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string", "description": "Full path to the video file"},
                                    "start": {"type": "number", "description": "Seconds from the clip's start"},
                                    "end": {"type": "number", "description": "Seconds from the clip's start"},
                                },
                                "required": ["path", "start", "end"],
                            },
                        },
                        "name": {
                            "type": "string",
                            "description": "Project name (default 'Reel <capture date>')",
                        },
                    },
                    "required": ["clips"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "video_status":
            return self._status()
        if tool_name == "video_project_create":
            return await self._create(args or {})
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _status(self) -> ToolResult:
        finder = _find_shotcut_fn if _find_shotcut_fn is not None else find_shotcut
        shotcut = finder()
        melt = find_melt(shotcut) if shotcut else None
        payload = {
            "available": bool(shotcut and melt),
            "shotcut": str(shotcut) if shotcut else None,
            "melt": str(melt) if melt else None,
        }
        if not payload["available"]:
            payload["hint"] = _INSTALL_MSG
        return ToolResult(content=json.dumps(payload))

    async def _create(self, args: dict) -> ToolResult:
        store = _shared.get("documents.store")
        pid = _shared.get("documents.profile_id")
        if store is None or pid is None:
            return ToolResult(content="Document library not ready (no active profile)", is_error=True)

        raw = args.get("clips") or []
        cuts: list[Cut] = []
        paths: list[Path] = []
        for i, c in enumerate(raw):
            try:
                path = Path(str(c["path"])).resolve()
                cut = Cut(str(path), float(c["start"]), float(c["end"]))
            except (KeyError, TypeError, ValueError):
                return ToolResult(content=f"clip {i}: needs path, start and end", is_error=True)
            if path.suffix.lower() not in _VIDEO_EXTS or not path.is_file():
                return ToolResult(content=f"clip {i}: not a video file: {path}", is_error=True)
            cuts.append(cut)
            paths.append(path)
        if not cuts:
            return ToolResult(content="clips is empty", is_error=True)

        probe = _probe_fn
        if probe is None:
            finder = _find_shotcut_fn if _find_shotcut_fn is not None else find_shotcut
            shotcut = finder()
            ffprobe = find_ffprobe(shotcut) if shotcut else None
            if ffprobe is None:
                return ToolResult(content=_INSTALL_MSG, is_error=True)
            probe = lambda p: probe_clip(p, ffprobe)  # noqa: E731
        try:
            infos = {str(p): await probe(p) for p in dict.fromkeys(paths)}
            xml = build_mlt(cuts, infos)
        except (ValueError, OSError) as exc:
            return ToolResult(content=str(exc), is_error=True)

        day = capture_date(paths)
        folder = project_folder(day)
        taken = {d["name"] for d in store.list_docs(pid) if d.get("folder") == folder}
        name = unique_name(taken, (args.get("name") or "").strip() or f"Reel {day:%Y-%m-%d}")
        doc = store.save_text(pid, folder, name, xml, ext="mlt")
        fp = fingerprint(xml)
        Path(doc["path"] + ".sha256").write_text(fp, encoding="utf-8")

        broadcast = _shared.get("documents.broadcast_fn")
        if broadcast:
            await broadcast()
        duration = sum(c.end - c.start for c in cuts)
        return ToolResult(content=json.dumps({
            "doc_id": doc["id"], "name": name, "folder": folder, "path": doc["path"],
            "clips": len(cuts), "duration_s": round(duration, 3), "fingerprint": fp,
        }))


def create() -> VideoProjectPlugin:
    return VideoProjectPlugin()
