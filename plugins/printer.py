"""
Printer/Scanner MCP plugin — Issue #27 (Hardware MCP — AFK).

Tools:
  - print_file(path, printer_name?)        — send a file to the default or
                                              named printer
  - print_queue(printer_name?)             — list jobs in the queue
  - print_list_printers()                  — return [{name}] of available
                                              printers
  - scan_document(output_path, format?)    — scan to file (POSIX only; the
                                              Windows branch returns a
                                              documented stub error rather
                                              than a fragile WIA bridge)

Platform-aware via injectable ``platform_name`` (defaults to ``sys.platform``):
  Windows  → PowerShell  Start-Process / Out-Printer / Get-PrintJob /
                          Get-Printer
  POSIX    → lp / lpstat / scanimage  (CUPS + SANE)

Side effects (``run_fn``) are injected so unit tests never invoke the real
binaries. Hardware-not-connected (non-zero exit / FileNotFoundError) surfaces
as ``is_error=True`` with the printer/scanner name in the message — same
fail-loud pattern as ``plugins/git.py`` etc.

Safety: this plugin is **output-only**. There is intentionally no
``print_remove_job`` / ``print_cancel_job`` / ``print_clear_queue`` tool —
removing jobs from another user's queue (or accidentally cancelling a print
mid-job) is exactly the kind of irreversible side effect Felix should not
have. Cancelling a job is a manual user action.
"""
import json
import re
import subprocess
import sys
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "printer"

_WINDOWS_SCAN_STUB = (
    "WIA scanning is not implemented on Windows. "
    "Use Windows Fax & Scan to scan documents manually, "
    "or run Felix on Linux/macOS where SANE (scanimage) is available."
)


class PrinterPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        run_fn: Callable | None = None,
        platform_name: str | None = None,
    ) -> None:
        self._run_fn = run_fn or subprocess.run
        self._platform_name = (
            platform_name if platform_name is not None else sys.platform
        )

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="print_file",
                description=(
                    "Send a file to the default or a named system printer. "
                    "Felix never cancels or removes existing jobs."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to the file to print.",
                        },
                        "printer_name": {
                            "type": "string",
                            "description": "Printer name (omit to use system default).",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="print_queue",
                description=(
                    "List jobs currently in a printer's queue. "
                    "If printer_name is omitted, shows the default printer."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "printer_name": {
                            "type": "string",
                            "description": "Printer name (omit for default).",
                        },
                    },
                },
            ),
            Tool(
                name="print_list_printers",
                description="List names of installed/available printers.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="scan_document",
                description=(
                    "Scan a document to a file (POSIX only via SANE/scanimage). "
                    "On Windows, this returns a documented stub-error pointing "
                    "to Windows Fax & Scan."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "output_path": {
                            "type": "string",
                            "description": "Output file path for the scan.",
                        },
                        "format": {
                            "type": "string",
                            "description": "Output format (pdf or png; default pdf).",
                        },
                    },
                    "required": ["output_path"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "print_file":
            return self._print_file(args)
        if tool_name == "print_queue":
            return self._print_queue(args)
        if tool_name == "print_list_printers":
            return self._print_list_printers()
        if tool_name == "scan_document":
            return self._scan_document(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # print_file
    # ------------------------------------------------------------------

    def _print_file(self, args: dict) -> ToolResult:
        path = args.get("path")
        if not path:
            return ToolResult(
                content="'path' is required for print_file",
                is_error=True,
            )
        printer = args.get("printer_name")
        if self._platform_name.startswith("win"):
            argv = self._windows_print_argv(path, printer)
        else:
            argv = ["lp"]
            if printer:
                argv += ["-d", printer]
            argv.append(path)
        return self._run(argv, target=printer or "default printer")

    def _windows_print_argv(self, path: str, printer: str | None) -> list[str]:
        if printer:
            # Out-Printer takes the printer name; Get-Content streams the file
            # body so plain-text and PDF (via the system PDF handler chain)
            # both end up at the named device.
            ps = (
                f'Get-Content -Path "{path}" -Raw | '
                f'Out-Printer -Name "{printer}"'
            )
        else:
            # Start-Process -Verb Print uses the default printer registered
            # for the file's extension.
            ps = f'Start-Process -FilePath "{path}" -Verb Print -Wait'
        return ["powershell", "-NoProfile", "-Command", ps]

    # ------------------------------------------------------------------
    # print_queue
    # ------------------------------------------------------------------

    def _print_queue(self, args: dict) -> ToolResult:
        printer = args.get("printer_name")
        if self._platform_name.startswith("win"):
            if printer:
                ps = f'Get-PrintJob -PrinterName "{printer}"'
            else:
                ps = "Get-PrintJob"
            argv = ["powershell", "-NoProfile", "-Command", ps]
        else:
            argv = ["lpstat", "-o"]
            if printer:
                argv.append(printer)
        return self._run(argv, target=printer or "default printer")

    # ------------------------------------------------------------------
    # print_list_printers
    # ------------------------------------------------------------------

    def _print_list_printers(self) -> ToolResult:
        if self._platform_name.startswith("win"):
            argv = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Printer | Select-Object -ExpandProperty Name",
            ]
        else:
            argv = ["lpstat", "-p"]

        try:
            proc = self._run_fn(
                argv, capture_output=True, text=True, timeout=15
            )
        except FileNotFoundError as exc:
            return ToolResult(
                content=f"printer command not found: {exc}",
                is_error=True,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                content="print_list_printers timed out", is_error=True
            )
        except Exception as exc:
            return ToolResult(
                content=f"print_list_printers failed: {exc}", is_error=True
            )

        if proc.returncode != 0:
            return ToolResult(
                content=json.dumps({
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "exit_code": proc.returncode,
                }),
                is_error=True,
            )

        if self._platform_name.startswith("win"):
            names = [
                line.strip()
                for line in (proc.stdout or "").splitlines()
                if line.strip()
            ]
        else:
            names = self._parse_lpstat_p(proc.stdout or "")

        return ToolResult(
            content=json.dumps({"printers": [{"name": n} for n in names]})
        )

    @staticmethod
    def _parse_lpstat_p(raw: str) -> list[str]:
        names: list[str] = []
        for line in raw.splitlines():
            match = re.match(r"^printer\s+(\S+)\s+is", line.strip())
            if match:
                names.append(match.group(1))
        return names

    # ------------------------------------------------------------------
    # scan_document
    # ------------------------------------------------------------------

    def _scan_document(self, args: dict) -> ToolResult:
        output_path = args.get("output_path")
        if not output_path:
            return ToolResult(
                content="'output_path' is required for scan_document",
                is_error=True,
            )
        if self._platform_name.startswith("win"):
            return ToolResult(content=_WINDOWS_SCAN_STUB, is_error=True)
        fmt = (args.get("format") or "pdf").lower()
        argv = [
            "scanimage",
            f"--format={fmt}",
            f"--output={output_path}",
        ]
        return self._run(argv, target="scanner")

    # ------------------------------------------------------------------
    # shared run helper — fail-loud on non-zero exit / missing binary
    # ------------------------------------------------------------------

    def _run(self, argv: list[str], *, target: str) -> ToolResult:
        try:
            proc = self._run_fn(
                argv, capture_output=True, text=True, timeout=120
            )
        except FileNotFoundError as exc:
            return ToolResult(
                content=f"hardware command not found for '{target}': {exc}",
                is_error=True,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                content=f"hardware command timed out for '{target}'",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                content=f"hardware command failed for '{target}': {exc}",
                is_error=True,
            )

        payload = json.dumps({
            "target": target,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
        return ToolResult(content=payload, is_error=proc.returncode != 0)


def create() -> PrinterPlugin:
    return PrinterPlugin()
