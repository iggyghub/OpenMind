"""
Printer/Scanner MCP plugin tests — Issue #27 (Hardware MCP — AFK).

Tools:
  - print_file(path, printer_name?)
  - print_queue(printer_name?)
  - print_list_printers()
  - scan_document(output_path, format?)

Platform-aware:
  Windows  → PowerShell Start-Process / Out-Printer / Get-PrintJob / Get-Printer
  POSIX    → lp / lpstat / scanimage

OS dispatch is parameterised by an injectable platform_name (default
sys.platform). All shell-outs are routed through an injectable run_fn so
tests never invoke real CLI binaries.
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


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    def test_create_plugin_named_printer(self):
        from plugins.printer import create

        assert create().name == "printer"

    def test_list_tools_exposes_four(self):
        from plugins.printer import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "print_file",
            "print_queue",
            "print_list_printers",
            "scan_document",
        }

    def test_print_file_requires_path(self):
        from plugins.printer import create

        tool = next(t for t in create().list_tools() if t.name == "print_file")
        assert "path" in tool.schema["required"]

    def test_scan_document_requires_output_path(self):
        from plugins.printer import create

        tool = next(t for t in create().list_tools() if t.name == "scan_document")
        assert "output_path" in tool.schema["required"]

    def test_no_destructive_tools_exposed(self):
        """Safety: this plugin is output-only. No remove/cancel/clear job tools."""
        from plugins.printer import create

        names = {t.name for t in create().list_tools()}
        forbidden = {
            "print_remove_job",
            "print_cancel_job",
            "print_clear_queue",
            "printer_remove",
            "printer_delete",
        }
        assert names.isdisjoint(forbidden)


# ---------------------------------------------------------------------------
# Cycle 2 — required-arg validation / safety
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_print_file_missing_path_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run()
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("print_file", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_print_file_empty_path_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run()
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("print_file", {"path": ""})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_scan_document_missing_output_path_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run()
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("scan_document", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_scan_document_empty_output_path_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run()
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("scan_document", {"output_path": ""})
        assert result.is_error
        assert captured["calls"] == 0


# ---------------------------------------------------------------------------
# Cycle 3 — POSIX (lp / lpstat / scanimage) branch
# ---------------------------------------------------------------------------


class TestPosixBranch:
    @pytest.mark.asyncio
    async def test_print_file_uses_lp_with_default_printer(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="request id is HP-LaserJet-42")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool(
            "print_file", {"path": "/tmp/report.pdf"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "lp"
        assert "/tmp/report.pdf" in argv
        # No -d flag when printer_name omitted (uses default)
        assert "-d" not in argv

    @pytest.mark.asyncio
    async def test_print_file_uses_lp_d_with_named_printer(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="request id is HP-LaserJet-43")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="darwin")
        await plugin.call_tool(
            "print_file",
            {"path": "/tmp/report.pdf", "printer_name": "HP-LaserJet"},
        )
        argv = captured["argv"]
        assert argv[0] == "lp"
        assert "-d" in argv
        assert "HP-LaserJet" in argv
        assert "/tmp/report.pdf" in argv

    @pytest.mark.asyncio
    async def test_print_queue_uses_lpstat_o(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="HP-LaserJet-42 user 1024 ...")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        await plugin.call_tool("print_queue", {"printer_name": "HP-LaserJet"})
        argv = captured["argv"]
        assert argv[0] == "lpstat"
        assert "-o" in argv
        assert "HP-LaserJet" in argv

    @pytest.mark.asyncio
    async def test_print_list_printers_uses_lpstat_p_and_parses(self):
        from plugins.printer import PrinterPlugin

        sample = (
            "printer HP-LaserJet is idle.  enabled since Mon May  4 10:00:00 2026\n"
            "printer Office-Brother is idle.  enabled since Mon May  4 10:00:00 2026\n"
        )
        run_fn, captured = _fake_run(stdout=sample)
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("print_list_printers", {})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "lpstat"
        assert "-p" in argv
        data = json.loads(result.content)
        names = {p["name"] for p in data["printers"]}
        assert names == {"HP-LaserJet", "Office-Brother"}

    @pytest.mark.asyncio
    async def test_scan_document_uses_scanimage_with_format(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool(
            "scan_document",
            {"output_path": "/tmp/scan.png", "format": "png"},
        )
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "scanimage"
        joined = " ".join(argv)
        assert "--format=png" in joined or ("--format" in argv and "png" in argv)
        assert "--output=/tmp/scan.png" in joined or (
            "--output" in argv and "/tmp/scan.png" in argv
        )

    @pytest.mark.asyncio
    async def test_scan_document_default_format_is_pdf(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        await plugin.call_tool("scan_document", {"output_path": "/tmp/scan.pdf"})
        joined = " ".join(captured["argv"])
        assert "pdf" in joined.lower()


# ---------------------------------------------------------------------------
# Cycle 4 — Windows (PowerShell) branch
# ---------------------------------------------------------------------------


class TestWindowsBranch:
    @pytest.mark.asyncio
    async def test_print_file_uses_powershell_default(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool(
            "print_file", {"path": r"C:\reports\q1.pdf"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0].lower() in ("powershell", "powershell.exe", "pwsh")
        joined = " ".join(argv)
        assert r"C:\reports\q1.pdf" in joined
        assert "Start-Process" in joined
        assert "Print" in joined

    @pytest.mark.asyncio
    async def test_print_file_uses_out_printer_with_named_printer(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="win32")
        await plugin.call_tool(
            "print_file",
            {"path": r"C:\reports\q1.pdf", "printer_name": "HP LaserJet"},
        )
        argv = captured["argv"]
        joined = " ".join(argv)
        assert "Out-Printer" in joined
        assert "HP LaserJet" in joined

    @pytest.mark.asyncio
    async def test_print_queue_uses_get_printjob(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="win32")
        await plugin.call_tool(
            "print_queue", {"printer_name": "HP LaserJet"}
        )
        joined = " ".join(captured["argv"])
        assert "Get-PrintJob" in joined
        assert "HP LaserJet" in joined

    @pytest.mark.asyncio
    async def test_print_list_printers_uses_get_printer(self):
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(
            stdout="HP LaserJet\nOffice Brother\n"
        )
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool("print_list_printers", {})
        joined = " ".join(captured["argv"])
        assert "Get-Printer" in joined
        assert not result.is_error
        data = json.loads(result.content)
        names = {p["name"] for p in data["printers"]}
        assert "HP LaserJet" in names
        assert "Office Brother" in names

    @pytest.mark.asyncio
    async def test_scan_document_on_windows_returns_documented_stub(self):
        """Windows scanning is not supported — should return a clear stub error
        rather than half-implementing a fragile WIA COM bridge."""
        from plugins.printer import PrinterPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="win32")
        result = await plugin.call_tool(
            "scan_document", {"output_path": r"C:\Users\me\scan.pdf"}
        )
        assert result.is_error
        assert captured["calls"] == 0
        # Helpful error message points to Fax & Scan
        assert "WIA" in result.content or "Fax" in result.content or "not implemented" in result.content.lower()


# ---------------------------------------------------------------------------
# Cycle 5 — hardware-not-connected / error paths
# ---------------------------------------------------------------------------


class TestHardwareNotConnected:
    @pytest.mark.asyncio
    async def test_print_file_nonzero_exit_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, _ = _fake_run(
            stderr="lp: The printer or class does not exist.", returncode=1
        )
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool(
            "print_file",
            {"path": "/tmp/x.pdf", "printer_name": "Nonexistent"},
        )
        assert result.is_error
        # Printer name surfaced in error
        assert "Nonexistent" in result.content

    @pytest.mark.asyncio
    async def test_print_file_filenotfounderror_returns_error(self):
        from plugins.printer import PrinterPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("lp not on PATH")

        plugin = PrinterPlugin(run_fn=boom, platform_name="linux")
        result = await plugin.call_tool(
            "print_file", {"path": "/tmp/x.pdf"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_scan_document_nonzero_exit_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, _ = _fake_run(
            stderr="scanimage: no SANE devices found", returncode=1
        )
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool(
            "scan_document", {"output_path": "/tmp/scan.pdf"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_scan_document_filenotfounderror_returns_error(self):
        from plugins.printer import PrinterPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("scanimage not installed")

        plugin = PrinterPlugin(run_fn=boom, platform_name="linux")
        result = await plugin.call_tool(
            "scan_document", {"output_path": "/tmp/scan.pdf"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.printer import PrinterPlugin

        run_fn, _ = _fake_run()
        plugin = PrinterPlugin(run_fn=run_fn, platform_name="linux")
        result = await plugin.call_tool("printer_nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — factory create()
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_returns_plugin_instance(self):
        from plugins.printer import PrinterPlugin, create

        assert isinstance(create(), PrinterPlugin)

    def test_factory_default_platform_is_sys_platform(self):
        import sys as _sys

        from plugins.printer import create

        plugin = create()
        assert plugin._platform_name == _sys.platform
