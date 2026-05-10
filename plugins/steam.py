"""
Steam game launcher MCP plugin — Issue #27 (Hardware MCP — AFK).

Tools:
  - steam_list_installed()                  — parse libraryfolders.vdf +
                                              appmanifest_*.acf for every
                                              library, return [{appid, name,
                                              installdir}].
  - steam_launch(name? | app_id?)           — launch via the
                                              ``steam://rungameid/<appid>``
                                              URL scheme. Requires an
                                              explicit name or app_id —
                                              there is no "launch the last
                                              game" default (safety).
  - steam_is_running(name? | app_id?)       — check whether a game's
                                              executable (derived from its
                                              installdir) is in the running
                                              process list.

All side effects are injectable so unit tests never touch the real Steam
install, never open the browser, and never enumerate real processes:
  steam_root      — Path to the Steam install (defaults to platform-specific
                    locations: Windows C:\\Program Files (x86)\\Steam, macOS
                    ~/Library/Application Support/Steam, Linux ~/.steam/steam
                    with fallback to ~/.local/share/Steam).
  launch_fn       — single-arg callable that opens a URL (defaults to
                    webbrowser.open — same pattern as plugins/zoom.py).
  process_iter    — zero-arg callable returning psutil-style process list
                    (defaults to psutil.process_iter — same as plugins/apps.py).

Hardware-not-connected equivalent: if the Steam root doesn't exist,
``steam_list_installed`` returns ``is_error=True`` with
``"Steam not installed at <path>"`` rather than crashing.

Tool naming: every tool is prefixed with ``steam_`` per the flat-global
namespace rule in ``.learnings/LEARNINGS.md`` (see #23 zoom/meet
``join_meeting`` collision).
"""
import json
import re
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "steam"

LaunchFn = Callable[[str], Any]
ProcessIterFn = Callable[[], list]


def _default_steam_root() -> Path:
    home = Path.home()
    if sys.platform.startswith("win"):
        return Path(r"C:\Program Files (x86)\Steam")
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Steam"
    primary = home / ".steam" / "steam"
    if primary.exists():
        return primary
    return home / ".local" / "share" / "Steam"


def _default_process_iter() -> list:
    try:
        import psutil

        return list(psutil.process_iter(["name", "pid", "exe", "cmdline"]))
    except Exception:
        return []


def _default_launch(url: str) -> None:
    webbrowser.open(url)


class SteamPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        steam_root: Path | str | None = None,
        *,
        launch_fn: LaunchFn | None = None,
        process_iter: ProcessIterFn | None = None,
    ) -> None:
        self._steam_root = (
            Path(steam_root) if steam_root is not None else _default_steam_root()
        )
        self._launch = launch_fn or _default_launch
        self._process_iter = process_iter or _default_process_iter

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="steam_list_installed",
                description=(
                    "List Steam games installed on this machine across all "
                    "Steam libraries. Returns [{appid, name, installdir}]."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="steam_launch",
                description=(
                    "Launch a Steam game. Requires either 'name' or 'app_id' "
                    "— there is no default game. Opens "
                    "steam://rungameid/<appid> via the system URL handler."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Game name (e.g. 'Cyberpunk 2077').",
                        },
                        "app_id": {
                            "type": "string",
                            "description": "Steam app ID (e.g. '1091500').",
                        },
                    },
                },
            ),
            Tool(
                name="steam_is_running",
                description=(
                    "Check whether a Steam game is currently running. "
                    "Heuristic match on the game's executable name derived "
                    "from its installdir."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "app_id": {"type": "string"},
                    },
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "steam_list_installed":
            return self._list_installed()
        if tool_name == "steam_launch":
            return self._launch_game(args)
        if tool_name == "steam_is_running":
            return self._is_running(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # steam_list_installed
    # ------------------------------------------------------------------

    def _list_installed(self) -> ToolResult:
        if not self._steam_root.exists():
            return ToolResult(
                content=f"Steam not installed at {self._steam_root}",
                is_error=True,
            )

        games: list[dict] = []
        for library in self._discover_libraries():
            steamapps = library / "steamapps"
            if not steamapps.is_dir():
                continue
            for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                game = self._parse_manifest(manifest)
                if game is not None:
                    games.append(game)
        return ToolResult(content=json.dumps({"games": games}))

    def _discover_libraries(self) -> list[Path]:
        """Read libraryfolders.vdf if present; fall back to the Steam root
        itself as the only library (its steamapps/ may still have
        manifests)."""
        libraries: list[Path] = []
        vdf = self._steam_root / "config" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                raw = vdf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
            for match in re.finditer(r'"path"\s*"([^"]+)"', raw):
                path = match.group(1).replace("\\\\", "\\")
                libraries.append(Path(path))
        if not libraries:
            libraries.append(self._steam_root)
        return libraries

    @staticmethod
    def _parse_manifest(path: Path) -> dict | None:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        appid = re.search(r'"appid"\s*"([^"]+)"', raw)
        name = re.search(r'"name"\s*"([^"]+)"', raw)
        installdir = re.search(r'"installdir"\s*"([^"]+)"', raw)
        if not (appid and name):
            return None
        return {
            "appid": appid.group(1),
            "name": name.group(1),
            "installdir": installdir.group(1) if installdir else "",
        }

    # ------------------------------------------------------------------
    # steam_launch
    # ------------------------------------------------------------------

    def _launch_game(self, args: dict) -> ToolResult:
        name = args.get("name")
        app_id = args.get("app_id")
        if not name and not app_id:
            return ToolResult(
                content=(
                    "'name' or 'app_id' is required for steam_launch — "
                    "Felix never auto-launches a default game"
                ),
                is_error=True,
            )

        if not app_id:
            game = self._find_game_by_name(name)
            if game is None:
                return ToolResult(
                    content=f"No installed Steam game matching name: '{name}'",
                    is_error=True,
                )
            app_id = game["appid"]

        url = f"steam://rungameid/{app_id}"
        try:
            self._launch(url)
        except Exception as exc:
            return ToolResult(
                content=f"Failed to launch Steam: {exc}",
                is_error=True,
            )
        return ToolResult(content=json.dumps({"launched": url}))

    # ------------------------------------------------------------------
    # steam_is_running
    # ------------------------------------------------------------------

    def _is_running(self, args: dict) -> ToolResult:
        name = args.get("name")
        app_id = args.get("app_id")
        if not name and not app_id:
            return ToolResult(
                content="'name' or 'app_id' is required for steam_is_running",
                is_error=True,
            )

        game = (
            self._find_game_by_appid(app_id)
            if app_id
            else self._find_game_by_name(name)
        )
        if game is None:
            ident = name or app_id
            return ToolResult(
                content=f"No installed Steam game matching: '{ident}'",
                is_error=True,
            )

        running = self._is_game_running(game)
        return ToolResult(content=json.dumps({
            "appid": game["appid"],
            "name": game["name"],
            "running": running,
        }))

    def _is_game_running(self, game: dict) -> bool:
        """Best-effort: Steam launches games as child processes whose name
        often matches the installdir (e.g. 'Cyberpunk 2077.exe' for
        'Cyberpunk 2077'). The appid is rarely in the cmdline, so we match
        on installdir-derived names."""
        haystack: list[str] = []
        installdir = (game.get("installdir") or "").lower()
        gamename = (game.get("name") or "").lower()
        try:
            for proc in self._process_iter():
                info = getattr(proc, "info", {}) or {}
                pname = (info.get("name") or "").lower()
                pexe = (info.get("exe") or "").lower()
                cmdline = " ".join(info.get("cmdline") or []).lower()
                haystack.append(pname)
                haystack.append(pexe)
                haystack.append(cmdline)
        except Exception:
            return False

        for needle in (installdir, gamename):
            if not needle:
                continue
            for hay in haystack:
                if needle in hay:
                    return True
        return False

    # ------------------------------------------------------------------
    # lookup helpers
    # ------------------------------------------------------------------

    def _find_game_by_name(self, name: str | None) -> dict | None:
        if not name:
            return None
        target = name.lower()
        for game in self._all_games():
            if game["name"].lower() == target:
                return game
        return None

    def _find_game_by_appid(self, appid: str | None) -> dict | None:
        if not appid:
            return None
        for game in self._all_games():
            if game["appid"] == str(appid):
                return game
        return None

    def _all_games(self) -> list[dict]:
        if not self._steam_root.exists():
            return []
        games: list[dict] = []
        for library in self._discover_libraries():
            steamapps = library / "steamapps"
            if not steamapps.is_dir():
                continue
            for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                parsed = self._parse_manifest(manifest)
                if parsed is not None:
                    games.append(parsed)
        return games


def create(
    steam_root: Path | str | None = None,
    *,
    launch_fn: LaunchFn | None = None,
    process_iter: ProcessIterFn | None = None,
) -> SteamPlugin:
    return SteamPlugin(
        steam_root=steam_root,
        launch_fn=launch_fn,
        process_iter=process_iter,
    )
