"""
VPN MCP plugin tests — Issue #26 (Security MCP — HITL).

Tools: vpn_connect(profile_name), vpn_disconnect(), vpn_status().

Platform-aware:
  Windows  → rasdial
  Darwin   → scutil --nc
  Linux    → nmcli connection

The OS dispatch is parameterised by an injectable platform string so tests
can exercise all three branches without monkey-patching sys.platform.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    captured: dict = {"argv": None, "kwargs": None, "calls": 0, "history": []}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["calls"] += 1
        captured["history"].append({"argv": list(argv), "kwargs": dict(kwargs)})
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    return runner, captured


def _fake_run_responses(responses: list):
    captured: dict = {"calls": 0, "history": []}

    def runner(argv, **kwargs):
        idx = captured["calls"]
        captured["calls"] += 1
        captured["history"].append({"argv": list(argv), "kwargs": dict(kwargs)})
        spec = responses[idx]
        return MagicMock(
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
            returncode=spec.get("returncode", 0),
        )

    return runner, captured


def _fake_http_get(payload: dict):
    """Return an http_get callable that returns this payload regardless of URL."""

    def fetch(url):
        return payload

    return fetch


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    def test_create_plugin_named_vpn(self):
        from plugins.vpn import create

        assert create().name == "vpn"

    def test_list_tools_exposes_three(self):
        from plugins.vpn import create

        names = {t.name for t in create().list_tools()}
        assert names == {"vpn_connect", "vpn_disconnect", "vpn_status"}

    def test_vpn_connect_requires_profile_name(self):
        from plugins.vpn import create

        tool = next(t for t in create().list_tools() if t.name == "vpn_connect")
        assert "profile_name" in tool.schema["required"]


# ---------------------------------------------------------------------------
# Cycle 2 — required args / safety
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_connect_missing_profile_returns_error(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("vpn_connect", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_connect_empty_profile_returns_error(self):
        """No auto-connect to a default — explicit profile required."""
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("vpn_connect", {"profile_name": ""})
        assert result.is_error
        assert captured["calls"] == 0


# ---------------------------------------------------------------------------
# Cycle 3 — Windows (rasdial) branch
# ---------------------------------------------------------------------------


class TestWindowsBranch:
    @pytest.mark.asyncio
    async def test_connect_uses_rasdial(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run(stdout="Successfully connected.")
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("vpn_connect", {"profile_name": "Work"})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "rasdial"
        assert "Work" in argv

    @pytest.mark.asyncio
    async def test_disconnect_uses_rasdial_disconnect(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run(stdout="Disconnected.")
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("vpn_disconnect", {})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "rasdial"
        assert "/disconnect" in argv or "/d" in argv


# ---------------------------------------------------------------------------
# Cycle 4 — macOS (scutil) branch
# ---------------------------------------------------------------------------


class TestMacBranch:
    @pytest.mark.asyncio
    async def test_connect_uses_scutil(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="darwin")
        await plugin.call_tool("vpn_connect", {"profile_name": "WorkVPN"})
        argv = captured["argv"]
        assert argv[0] == "scutil"
        assert "--nc" in argv
        assert "start" in argv
        assert "WorkVPN" in argv

    @pytest.mark.asyncio
    async def test_disconnect_uses_scutil_stop(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="darwin")
        # On macOS, stopping needs the active connection name; we expect the
        # plugin to track the most recent profile, OR to require it as an arg.
        # We require it to track the last profile from connect.
        await plugin.call_tool("vpn_connect", {"profile_name": "WorkVPN"})
        await plugin.call_tool("vpn_disconnect", {})
        argv = captured["history"][-1]["argv"]
        assert argv[0] == "scutil"
        assert "--nc" in argv
        assert "stop" in argv


# ---------------------------------------------------------------------------
# Cycle 5 — Linux (nmcli) branch
# ---------------------------------------------------------------------------


class TestLinuxBranch:
    @pytest.mark.asyncio
    async def test_connect_uses_nmcli(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="linux")
        await plugin.call_tool("vpn_connect", {"profile_name": "WorkVPN"})
        argv = captured["argv"]
        assert argv[0] == "nmcli"
        assert "connection" in argv
        assert "up" in argv
        assert "WorkVPN" in argv

    @pytest.mark.asyncio
    async def test_disconnect_uses_nmcli_down(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="linux")
        await plugin.call_tool("vpn_connect", {"profile_name": "WorkVPN"})
        await plugin.call_tool("vpn_disconnect", {})
        argv = captured["history"][-1]["argv"]
        assert argv[0] == "nmcli"
        assert "down" in argv


# ---------------------------------------------------------------------------
# Cycle 6 — vpn_status (uses external IP fetcher)
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_disconnected_when_no_active_profile(self):
        from plugins.vpn import VpnPlugin

        # Windows rasdial with no connections returns non-zero
        run_fn, _ = _fake_run(stdout="No connections", returncode=1)
        plugin = VpnPlugin(
            run_fn=run_fn,
            platform_name="win32",
            http_get=_fake_http_get({"query": "1.2.3.4"}),
        )
        result = await plugin.call_tool("vpn_status", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["connected"] is False

    @pytest.mark.asyncio
    async def test_status_connected_returns_profile_and_ip(self):
        from plugins.vpn import VpnPlugin

        run_fn, _ = _fake_run(
            stdout="Connected to Work\nStatus: Connected", returncode=0
        )
        plugin = VpnPlugin(
            run_fn=run_fn,
            platform_name="win32",
            http_get=_fake_http_get({"query": "10.0.0.1"}),
        )
        await plugin.call_tool("vpn_connect", {"profile_name": "Work"})
        result = await plugin.call_tool("vpn_status", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["connected"] is True
        assert data["profile"] == "Work"
        assert data["ip"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_status_handles_http_get_failure(self):
        from plugins.vpn import VpnPlugin

        def boom(url):
            raise OSError("network unreachable")

        run_fn, _ = _fake_run(stdout="Connected", returncode=0)
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32", http_get=boom)
        await plugin.call_tool("vpn_connect", {"profile_name": "Work"})
        result = await plugin.call_tool("vpn_status", {})
        # Status call should still succeed with ip=None — failing to fetch IP
        # shouldn't break the whole status check.
        assert not result.is_error
        data = json.loads(result.content)
        assert data["ip"] is None or data["ip"] == ""


# ---------------------------------------------------------------------------
# Cycle 7 — error paths
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.asyncio
    async def test_connect_failure_returns_error(self):
        from plugins.vpn import VpnPlugin

        run_fn, _ = _fake_run(stderr="Profile not found", returncode=1)
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("vpn_connect", {"profile_name": "Nope"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_fn_raises_returns_error(self):
        from plugins.vpn import VpnPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("rasdial not on PATH")

        plugin = VpnPlugin(run_fn=boom, platform_name="win32")
        result = await plugin.call_tool("vpn_connect", {"profile_name": "X"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.vpn import VpnPlugin

        run_fn, _ = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("vpn_nope", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unsupported_platform_returns_error(self):
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="haiku")
        result = await plugin.call_tool("vpn_connect", {"profile_name": "X"})
        assert result.is_error
        assert captured["calls"] == 0


# ---------------------------------------------------------------------------
# Cycle 8 — disconnect without prior connect
# ---------------------------------------------------------------------------


class TestDisconnectEdgeCases:
    @pytest.mark.asyncio
    async def test_disconnect_without_connect_on_windows_still_calls_rasdial(self):
        """On Windows rasdial /disconnect needs no profile name."""
        from plugins.vpn import VpnPlugin

        run_fn, captured = _fake_run()
        plugin = VpnPlugin(run_fn=run_fn, platform_name="win32")
        await plugin.call_tool("vpn_disconnect", {})
        argv = captured["argv"]
        assert argv[0] == "rasdial"


# ---------------------------------------------------------------------------
# Cycle 9 — factory create()
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_returns_plugin_instance(self):
        from plugins.vpn import VpnPlugin, create

        plugin = create()
        assert isinstance(plugin, VpnPlugin)

    def test_factory_default_platform_is_sys_platform(self):
        import sys as _sys
        from plugins.vpn import create

        plugin = create()
        assert plugin._platform_name == _sys.platform
