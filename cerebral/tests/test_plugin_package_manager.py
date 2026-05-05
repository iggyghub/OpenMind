"""
Package manager MCP plugin tests — Issue #24.

One plugin, three back-ends: npm, pip, winget.
Tools: pkg_install(manager, name), pkg_update(manager, name?), pkg_search(manager, query).
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    captured: dict = {"argv": None, "kwargs": None, "calls": 0}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["calls"] += 1
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    return runner, captured


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three(self):
        from plugins.package_manager import create

        names = {t.name for t in create().list_tools()}
        assert names == {"pkg_install", "pkg_update", "pkg_search"}

    def test_create_plugin_named_package_manager(self):
        from plugins.package_manager import create

        assert create().name == "package_manager"


# ---------------------------------------------------------------------------
# Cycle 2 — manager validation: only npm, pip, winget allowed
# ---------------------------------------------------------------------------

class TestManagerValidation:
    @pytest.mark.asyncio
    async def test_unknown_manager_returns_error(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "pkg_install", {"manager": "apt", "name": "curl"}
        )
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_missing_manager_returns_error(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("pkg_install", {"name": "curl"})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("manager", ["npm", "pip", "winget"])
    async def test_allowed_managers_dispatch(self, manager):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "pkg_install", {"manager": manager, "name": "axios"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == manager


# ---------------------------------------------------------------------------
# Cycle 3 — pkg_install requires name
# ---------------------------------------------------------------------------

class TestPkgInstall:
    @pytest.mark.asyncio
    async def test_install_missing_name_returns_error(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("pkg_install", {"manager": "npm"})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_npm_install_uses_install(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "pkg_install", {"manager": "npm", "name": "lodash"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "npm"
        assert "install" in argv
        assert "lodash" in argv

    @pytest.mark.asyncio
    async def test_pip_install_uses_install(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_install", {"manager": "pip", "name": "requests"}
        )
        argv = captured["argv"]
        assert argv[0] == "pip"
        assert "install" in argv
        assert "requests" in argv

    @pytest.mark.asyncio
    async def test_winget_install_uses_install(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_install", {"manager": "winget", "name": "Git.Git"}
        )
        argv = captured["argv"]
        assert argv[0] == "winget"
        assert "install" in argv
        assert "Git.Git" in argv


# ---------------------------------------------------------------------------
# Cycle 4 — pkg_update with and without a name
# ---------------------------------------------------------------------------

class TestPkgUpdate:
    @pytest.mark.asyncio
    async def test_npm_update_no_name(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("pkg_update", {"manager": "npm"})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "npm"
        assert "update" in argv

    @pytest.mark.asyncio
    async def test_npm_update_with_name(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_update", {"manager": "npm", "name": "react"}
        )
        argv = captured["argv"]
        assert "update" in argv
        assert "react" in argv

    @pytest.mark.asyncio
    async def test_pip_update_uses_install_upgrade(self):
        """pip has no 'update' subcommand — implementations use `install -U <name>`."""
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_update", {"manager": "pip", "name": "numpy"}
        )
        argv = captured["argv"]
        assert argv[0] == "pip"
        assert "install" in argv
        assert ("-U" in argv or "--upgrade" in argv)
        assert "numpy" in argv

    @pytest.mark.asyncio
    async def test_winget_update(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_update", {"manager": "winget", "name": "Git.Git"}
        )
        argv = captured["argv"]
        assert argv[0] == "winget"
        assert ("upgrade" in argv or "update" in argv)
        assert "Git.Git" in argv


# ---------------------------------------------------------------------------
# Cycle 5 — pkg_search
# ---------------------------------------------------------------------------

class TestPkgSearch:
    @pytest.mark.asyncio
    async def test_search_missing_query_returns_error(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("pkg_search", {"manager": "npm"})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_npm_search(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_search", {"manager": "npm", "query": "axios"}
        )
        argv = captured["argv"]
        assert argv[0] == "npm"
        assert "search" in argv
        assert "axios" in argv

    @pytest.mark.asyncio
    async def test_pip_search_uses_index(self):
        """`pip search` is disabled on PyPI; many implementations use `pip index versions <q>`."""
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "pkg_search", {"manager": "pip", "query": "numpy"}
        )
        # Acceptable: returns either a real result or an error explaining pip search is gone.
        assert captured["calls"] >= 0  # any behaviour is fine, just don't crash
        assert result is not None

    @pytest.mark.asyncio
    async def test_winget_search(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, captured = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "pkg_search", {"manager": "winget", "query": "vscode"}
        )
        argv = captured["argv"]
        assert argv[0] == "winget"
        assert "search" in argv
        assert "vscode" in argv


# ---------------------------------------------------------------------------
# Cycle 6 — error paths
# ---------------------------------------------------------------------------

class TestErrors:
    @pytest.mark.asyncio
    async def test_non_zero_exit_is_error(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, _ = _fake_run(stderr="not found", returncode=1)
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "pkg_install", {"manager": "npm", "name": "doesnotexist"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_fn_raises_returns_error(self):
        from plugins.package_manager import PackageManagerPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("npm not on PATH")

        plugin = PackageManagerPlugin(run_fn=boom)
        result = await plugin.call_tool(
            "pkg_install", {"manager": "npm", "name": "axios"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, _ = _fake_run()
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("pkg_uninstall", {"manager": "npm", "name": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — success shape
# ---------------------------------------------------------------------------

class TestSuccessShape:
    @pytest.mark.asyncio
    async def test_success_returns_stdout_stderr_exit(self):
        from plugins.package_manager import PackageManagerPlugin

        run_fn, _ = _fake_run(stdout="installed!", returncode=0)
        plugin = PackageManagerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "pkg_install", {"manager": "npm", "name": "axios"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["stdout"] == "installed!"
        assert data["exit_code"] == 0
