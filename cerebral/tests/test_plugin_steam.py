"""
Steam MCP plugin tests — Issue #27 (Hardware MCP — AFK).

Tools:
  - steam_list_installed()                  — parse libraryfolders.vdf +
                                              appmanifest_*.acf
  - steam_launch(name? | app_id?)           — launch via
                                              steam://rungameid/<appid>
  - steam_is_running(name? | app_id?)       — check via psutil.process_iter

All side effects (filesystem via tmp_path, launch_fn, process_iter,
steam_root) are injected so tests never touch the real Steam install,
never open the browser, and never enumerate real processes.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Fixtures: build a minimal Steam library on disk
# ---------------------------------------------------------------------------


def _write_library(root: Path, libs: list[Path]):
    """Write a minimal libraryfolders.vdf listing the given library roots."""
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    vdf = '"libraryfolders"\n{\n'
    for i, lib in enumerate(libs):
        # Steam's vdf format escapes backslashes — mimic that.
        path_str = str(lib).replace("\\", "\\\\")
        vdf += (
            f'\t"{i}"\n'
            "\t{\n"
            f'\t\t"path"\t\t"{path_str}"\n'
            "\t}\n"
        )
    vdf += "}\n"
    (config_dir / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")


def _write_manifest(library: Path, appid: str, name: str, installdir: str):
    steamapps = library / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    manifest = (
        '"AppState"\n'
        "{\n"
        f'\t"appid"\t\t"{appid}"\n'
        f'\t"name"\t\t"{name}"\n'
        f'\t"installdir"\t"{installdir}"\n'
        "}\n"
    )
    (steamapps / f"appmanifest_{appid}.acf").write_text(manifest, encoding="utf-8")


@pytest.fixture
def steam_install(tmp_path):
    """Build a fake Steam install with two installed games."""
    root = tmp_path / "Steam"
    library = root  # primary library is the Steam root itself
    extra_lib = tmp_path / "SteamLibrary"
    _write_library(root, [library, extra_lib])
    # CS2 in the primary library — appid 730 is a stable real example.
    _write_manifest(library, "730", "Counter-Strike 2", "Counter-Strike Global Offensive")
    # A second game in the extra library
    _write_manifest(extra_lib, "1091500", "Cyberpunk 2077", "Cyberpunk 2077")
    return root


def _capturing_launch():
    captured = {"calls": 0, "urls": []}

    def launch(url):
        captured["calls"] += 1
        captured["urls"].append(url)

    return launch, captured


def _proc_iter_factory(processes: list[dict]):
    """Build a process_iter callable returning MagicMock-shaped psutil entries."""

    def _iter():
        out = []
        for spec in processes:
            mock = MagicMock()
            mock.info = {
                "name": spec.get("name", ""),
                "pid": spec.get("pid", 0),
                "exe": spec.get("exe", ""),
                "cmdline": spec.get("cmdline", []),
            }
            out.append(mock)
        return out

    return _iter


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    def test_create_plugin_named_steam(self):
        from plugins.steam import create

        assert create().name == "steam"

    def test_list_tools_exposes_three(self):
        from plugins.steam import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "steam_list_installed",
            "steam_launch",
            "steam_is_running",
        }


# ---------------------------------------------------------------------------
# Cycle 2 — required-arg validation / safety
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_launch_requires_name_or_app_id(self, steam_install):
        """Safety: no 'launch the last/default game' default. Must be explicit."""
        from plugins.steam import SteamPlugin

        launch, captured = _capturing_launch()
        plugin = SteamPlugin(steam_root=steam_install, launch_fn=launch)
        result = await plugin.call_tool("steam_launch", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_launch_empty_name_returns_error(self, steam_install):
        from plugins.steam import SteamPlugin

        launch, captured = _capturing_launch()
        plugin = SteamPlugin(steam_root=steam_install, launch_fn=launch)
        result = await plugin.call_tool("steam_launch", {"name": ""})
        assert result.is_error
        assert captured["calls"] == 0


# ---------------------------------------------------------------------------
# Cycle 3 — steam_list_installed (libraryfolders + appmanifest parsing)
# ---------------------------------------------------------------------------


class TestListInstalled:
    @pytest.mark.asyncio
    async def test_lists_games_across_libraries(self, steam_install):
        from plugins.steam import SteamPlugin

        plugin = SteamPlugin(steam_root=steam_install)
        result = await plugin.call_tool("steam_list_installed", {})
        assert not result.is_error
        data = json.loads(result.content)
        names = {g["name"] for g in data["games"]}
        appids = {g["appid"] for g in data["games"]}
        assert names == {"Counter-Strike 2", "Cyberpunk 2077"}
        assert appids == {"730", "1091500"}

    @pytest.mark.asyncio
    async def test_each_game_has_install_dir(self, steam_install):
        from plugins.steam import SteamPlugin

        plugin = SteamPlugin(steam_root=steam_install)
        result = await plugin.call_tool("steam_list_installed", {})
        data = json.loads(result.content)
        for g in data["games"]:
            assert "installdir" in g
            assert g["installdir"]

    @pytest.mark.asyncio
    async def test_missing_steam_root_returns_error(self, tmp_path):
        from plugins.steam import SteamPlugin

        plugin = SteamPlugin(steam_root=tmp_path / "NoSteamHere")
        result = await plugin.call_tool("steam_list_installed", {})
        assert result.is_error
        assert "Steam not installed" in result.content

    @pytest.mark.asyncio
    async def test_missing_libraryfolders_falls_back_to_steam_root(self, tmp_path):
        """If libraryfolders.vdf is absent, the Steam root itself is used as
        the only library — its steamapps/ may still have manifests."""
        from plugins.steam import SteamPlugin

        root = tmp_path / "Steam"
        root.mkdir()
        _write_manifest(root, "440", "Team Fortress 2", "Team Fortress 2")
        plugin = SteamPlugin(steam_root=root)
        result = await plugin.call_tool("steam_list_installed", {})
        assert not result.is_error
        data = json.loads(result.content)
        names = {g["name"] for g in data["games"]}
        assert "Team Fortress 2" in names


# ---------------------------------------------------------------------------
# Cycle 4 — steam_launch (URL scheme via injectable launch_fn)
# ---------------------------------------------------------------------------


class TestLaunch:
    @pytest.mark.asyncio
    async def test_launch_by_app_id_uses_rungameid_url(self, steam_install):
        from plugins.steam import SteamPlugin

        launch, captured = _capturing_launch()
        plugin = SteamPlugin(steam_root=steam_install, launch_fn=launch)
        result = await plugin.call_tool("steam_launch", {"app_id": "730"})
        assert not result.is_error
        assert captured["calls"] == 1
        url = captured["urls"][0]
        assert url == "steam://rungameid/730"

    @pytest.mark.asyncio
    async def test_launch_by_name_resolves_appid(self, steam_install):
        from plugins.steam import SteamPlugin

        launch, captured = _capturing_launch()
        plugin = SteamPlugin(steam_root=steam_install, launch_fn=launch)
        result = await plugin.call_tool(
            "steam_launch", {"name": "Cyberpunk 2077"}
        )
        assert not result.is_error
        assert captured["urls"] == ["steam://rungameid/1091500"]

    @pytest.mark.asyncio
    async def test_launch_by_name_is_case_insensitive(self, steam_install):
        from plugins.steam import SteamPlugin

        launch, captured = _capturing_launch()
        plugin = SteamPlugin(steam_root=steam_install, launch_fn=launch)
        result = await plugin.call_tool(
            "steam_launch", {"name": "cyberpunk 2077"}
        )
        assert not result.is_error
        assert captured["urls"] == ["steam://rungameid/1091500"]

    @pytest.mark.asyncio
    async def test_launch_unknown_name_returns_error(self, steam_install):
        from plugins.steam import SteamPlugin

        launch, captured = _capturing_launch()
        plugin = SteamPlugin(steam_root=steam_install, launch_fn=launch)
        result = await plugin.call_tool(
            "steam_launch", {"name": "Half-Life 3"}
        )
        assert result.is_error
        assert captured["calls"] == 0
        assert "Half-Life 3" in result.content


# ---------------------------------------------------------------------------
# Cycle 5 — steam_is_running (process matching via injected process_iter)
# ---------------------------------------------------------------------------


class TestIsRunning:
    @pytest.mark.asyncio
    async def test_running_match_by_executable_name(self, steam_install):
        """Match the game's installdir-derived exe name in process list."""
        from plugins.steam import SteamPlugin

        proc_iter = _proc_iter_factory([
            {"name": "Cyberpunk 2077.exe", "pid": 4242},
            {"name": "explorer.exe", "pid": 1},
        ])
        plugin = SteamPlugin(
            steam_root=steam_install, process_iter=proc_iter
        )
        result = await plugin.call_tool(
            "steam_is_running", {"name": "Cyberpunk 2077"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["running"] is True

    @pytest.mark.asyncio
    async def test_not_running_when_no_matching_process(self, steam_install):
        from plugins.steam import SteamPlugin

        proc_iter = _proc_iter_factory([
            {"name": "explorer.exe", "pid": 1},
        ])
        plugin = SteamPlugin(
            steam_root=steam_install, process_iter=proc_iter
        )
        result = await plugin.call_tool(
            "steam_is_running", {"app_id": "730"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["running"] is False

    @pytest.mark.asyncio
    async def test_is_running_requires_name_or_app_id(self, steam_install):
        from plugins.steam import SteamPlugin

        proc_iter = _proc_iter_factory([])
        plugin = SteamPlugin(
            steam_root=steam_install, process_iter=proc_iter
        )
        result = await plugin.call_tool("steam_is_running", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_is_running_unknown_name_returns_error(self, steam_install):
        from plugins.steam import SteamPlugin

        proc_iter = _proc_iter_factory([])
        plugin = SteamPlugin(
            steam_root=steam_install, process_iter=proc_iter
        )
        result = await plugin.call_tool(
            "steam_is_running", {"name": "Nope"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — error paths / unknown tool
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, steam_install):
        from plugins.steam import SteamPlugin

        plugin = SteamPlugin(steam_root=steam_install)
        result = await plugin.call_tool("steam_nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — factory create()
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_returns_plugin_instance(self):
        from plugins.steam import SteamPlugin, create

        assert isinstance(create(), SteamPlugin)

    def test_factory_default_steam_root_is_platform_specific(self):
        """Default steam_root depends on platform — just confirm one is set."""
        from plugins.steam import create

        plugin = create()
        assert plugin._steam_root is not None
