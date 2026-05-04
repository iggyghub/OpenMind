"""
Network Scanner MCP plugin tests — Issue #26 (Security MCP — HITL).

Tools:
  - net_list_devices() — parses `arp -a` for IP/hostname pairs.
  - net_ping(host, count?) — shells out to `ping`. Note: Windows uses `-n`,
    POSIX uses `-c`; both branches are exercised here.
  - net_check_port(host, port, timeout?) — opens a TCP connection via an
    injectable socket_factory so tests never touch a real socket.
"""
import json
import socket
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


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    def test_create_plugin_named_network_scanner(self):
        from plugins.network_scanner import create

        assert create().name == "network_scanner"

    def test_list_tools_exposes_three(self):
        from plugins.network_scanner import create

        names = {t.name for t in create().list_tools()}
        assert names == {"net_list_devices", "net_ping", "net_check_port"}

    def test_ping_requires_host(self):
        from plugins.network_scanner import create

        tool = next(t for t in create().list_tools() if t.name == "net_ping")
        assert "host" in tool.schema["required"]

    def test_check_port_requires_host_and_port(self):
        from plugins.network_scanner import create

        tool = next(t for t in create().list_tools() if t.name == "net_check_port")
        assert "host" in tool.schema["required"]
        assert "port" in tool.schema["required"]


# ---------------------------------------------------------------------------
# Cycle 2 — net_list_devices uses arp -a
# ---------------------------------------------------------------------------


_ARP_SAMPLE_WIN = (
    "Interface: 192.168.1.10 --- 0xb\n"
    "  Internet Address      Physical Address      Type\n"
    "  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic\n"
    "  192.168.1.55          11-22-33-44-55-66     dynamic\n"
)

_ARP_SAMPLE_POSIX = (
    "router.local (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
    "? (192.168.1.55) at 11:22:33:44:55:66 on en0 ifscope [ethernet]\n"
)


class TestListDevices:
    @pytest.mark.asyncio
    async def test_list_devices_calls_arp_a(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, captured = _fake_run(stdout=_ARP_SAMPLE_WIN)
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("net_list_devices", {})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "arp"
        assert "-a" in argv

    @pytest.mark.asyncio
    async def test_list_devices_parses_windows_format(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run(stdout=_ARP_SAMPLE_WIN)
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("net_list_devices", {})
        data = json.loads(result.content)
        ips = {d["ip"] for d in data["devices"]}
        assert "192.168.1.1" in ips
        assert "192.168.1.55" in ips

    @pytest.mark.asyncio
    async def test_list_devices_parses_posix_format(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run(stdout=_ARP_SAMPLE_POSIX)
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="darwin")
        result = await plugin.call_tool("net_list_devices", {})
        data = json.loads(result.content)
        ips = {d["ip"] for d in data["devices"]}
        assert "192.168.1.1" in ips
        assert "192.168.1.55" in ips

    @pytest.mark.asyncio
    async def test_list_devices_includes_hostname_when_available(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run(stdout=_ARP_SAMPLE_POSIX)
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="darwin")
        result = await plugin.call_tool("net_list_devices", {})
        data = json.loads(result.content)
        router = next((d for d in data["devices"] if d["ip"] == "192.168.1.1"), None)
        assert router is not None
        assert router.get("hostname") == "router.local"

    @pytest.mark.asyncio
    async def test_list_devices_returns_empty_on_no_arp_output(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run(stdout="")
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("net_list_devices", {})
        data = json.loads(result.content)
        assert data["devices"] == []

    @pytest.mark.asyncio
    async def test_list_devices_run_fn_raises_returns_error(self):
        from plugins.network_scanner import NetworkScannerPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("arp not on PATH")

        plugin = NetworkScannerPlugin(run_fn=boom, platform_name="linux")
        result = await plugin.call_tool("net_list_devices", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — net_ping argv shape (Windows uses -n, POSIX uses -c)
# ---------------------------------------------------------------------------


class TestPing:
    @pytest.mark.asyncio
    async def test_ping_missing_host_returns_error(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, captured = _fake_run()
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("net_ping", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_ping_windows_uses_dash_n_flag(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, captured = _fake_run(
            stdout="Reply from 8.8.8.8: bytes=32 time=15ms TTL=117"
        )
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="win32")
        await plugin.call_tool("net_ping", {"host": "8.8.8.8", "count": 4})
        argv = captured["argv"]
        assert argv[0] == "ping"
        assert "-n" in argv
        assert "4" in argv
        assert "8.8.8.8" in argv

    @pytest.mark.asyncio
    async def test_ping_linux_uses_dash_c_flag(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, captured = _fake_run(stdout="64 bytes from 8.8.8.8: icmp_seq=1")
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="linux")
        await plugin.call_tool("net_ping", {"host": "8.8.8.8", "count": 3})
        argv = captured["argv"]
        assert argv[0] == "ping"
        assert "-c" in argv
        assert "3" in argv

    @pytest.mark.asyncio
    async def test_ping_macos_uses_dash_c_flag(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, captured = _fake_run(stdout="64 bytes from 8.8.8.8: icmp_seq=1")
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="darwin")
        await plugin.call_tool("net_ping", {"host": "8.8.8.8"})
        argv = captured["argv"]
        assert argv[0] == "ping"
        assert "-c" in argv

    @pytest.mark.asyncio
    async def test_ping_default_count(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, captured = _fake_run(stdout="ok")
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="linux")
        await plugin.call_tool("net_ping", {"host": "1.1.1.1"})
        argv = captured["argv"]
        # default count of 4 is convention; just assert there's a numeric arg
        assert any(a.isdigit() for a in argv)

    @pytest.mark.asyncio
    async def test_ping_returns_stdout_stderr_exit_on_success(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run(stdout="reply ok", returncode=0)
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("net_ping", {"host": "1.1.1.1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert "stdout" in data
        assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_ping_non_zero_exit_is_error(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run(stderr="unreachable", returncode=1)
        plugin = NetworkScannerPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("net_ping", {"host": "10.99.99.99"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — net_check_port uses injected socket_factory
# ---------------------------------------------------------------------------


def _socket_factory_open():
    """Returns (factory, calls). The factory pretends every connection succeeds."""
    calls = []

    def factory(addr, timeout):
        calls.append({"addr": addr, "timeout": timeout})
        return MagicMock(close=MagicMock())

    return factory, calls


def _socket_factory_refused():
    calls = []

    def factory(addr, timeout):
        calls.append({"addr": addr, "timeout": timeout})
        raise ConnectionRefusedError("refused")

    return factory, calls


def _socket_factory_timeout():
    calls = []

    def factory(addr, timeout):
        calls.append({"addr": addr, "timeout": timeout})
        raise socket.timeout("timed out")

    return factory, calls


class TestCheckPort:
    @pytest.mark.asyncio
    async def test_check_port_missing_host_returns_error(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, calls = _socket_factory_open()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        result = await plugin.call_tool("net_check_port", {"port": 80})
        assert result.is_error
        assert calls == []

    @pytest.mark.asyncio
    async def test_check_port_missing_port_returns_error(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, calls = _socket_factory_open()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        result = await plugin.call_tool("net_check_port", {"host": "1.1.1.1"})
        assert result.is_error
        assert calls == []

    @pytest.mark.asyncio
    async def test_check_port_open_returns_open_true(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, calls = _socket_factory_open()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        result = await plugin.call_tool(
            "net_check_port", {"host": "1.1.1.1", "port": 443}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["open"] is True
        assert data["host"] == "1.1.1.1"
        assert data["port"] == 443
        assert calls[0]["addr"] == ("1.1.1.1", 443)

    @pytest.mark.asyncio
    async def test_check_port_refused_returns_open_false(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, _ = _socket_factory_refused()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        result = await plugin.call_tool(
            "net_check_port", {"host": "1.1.1.1", "port": 9999}
        )
        # Refused is not an error — the call itself succeeded, the port is closed
        assert not result.is_error
        data = json.loads(result.content)
        assert data["open"] is False

    @pytest.mark.asyncio
    async def test_check_port_timeout_returns_open_false(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, _ = _socket_factory_timeout()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        result = await plugin.call_tool(
            "net_check_port", {"host": "1.1.1.1", "port": 22}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["open"] is False

    @pytest.mark.asyncio
    async def test_check_port_uses_custom_timeout(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, calls = _socket_factory_open()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        await plugin.call_tool(
            "net_check_port", {"host": "1.1.1.1", "port": 80, "timeout": 1.5}
        )
        assert calls[0]["timeout"] == 1.5

    @pytest.mark.asyncio
    async def test_check_port_default_timeout(self):
        from plugins.network_scanner import NetworkScannerPlugin

        factory, calls = _socket_factory_open()
        plugin = NetworkScannerPlugin(socket_factory=factory)
        await plugin.call_tool("net_check_port", {"host": "1.1.1.1", "port": 80})
        assert calls[0]["timeout"] is not None and calls[0]["timeout"] > 0


# ---------------------------------------------------------------------------
# Cycle 5 — unknown tool / factory
# ---------------------------------------------------------------------------


class TestMisc:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.network_scanner import NetworkScannerPlugin

        run_fn, _ = _fake_run()
        plugin = NetworkScannerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("net_nope", {})
        assert result.is_error

    def test_create_factory(self):
        from plugins.network_scanner import NetworkScannerPlugin, create

        plugin = create()
        assert isinstance(plugin, NetworkScannerPlugin)
