"""
Bitwarden MCP plugin — Issue #26 (Security MCP — HITL).

Tools (READ-ONLY):
  - bw_unlock(master_password) — unlocks the local Bitwarden vault for the
    duration of this Cerebral process. Captures the session token in RAM only.
  - bw_get_item(name) — returns the item payload (login/notes/card/identity)
    for a single vault item. Requires a successful bw_unlock first.
  - bw_list_items(folder?) — returns vault items, optionally filtered by a
    folder name (matched as a free-text search).

Security contract (do not relax without security review):
  * READ-ONLY by design. No bw_create / bw_edit / bw_delete tool exists. Do
    not add one — the LLM should never mutate the vault unattended.
  * The master password is passed in by the caller (the LLM, prompted by
    Felix's HITL flow). It is forwarded straight to `bw unlock --raw` via
    stdin, then released. It is never assigned to a plugin attribute, never
    logged, and never written to disk.
  * The session token returned by `bw unlock --raw` lives only in
    `self._session_token` for the lifetime of the process. It is passed to
    subsequent `bw` invocations through the BW_SESSION env var (per the
    Bitwarden CLI contract) and is never echoed back to the LLM.

Requires the `bw` CLI on PATH. If the binary is missing, run_fn raises and
this plugin returns is_error=True — the same fail-loud pattern used by
plugins/shell.py and plugins/git.py.
"""
import json
import os
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "bitwarden"


class BitwardenPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run
        # RAM-only — never persisted, never logged.
        self._session_token: str | None = None

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="bw_unlock",
                description=(
                    "Unlock the local Bitwarden vault for this session. The "
                    "master password is held only long enough to call "
                    "`bw unlock` and is never persisted."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "master_password": {
                            "type": "string",
                            "description": "Bitwarden master password.",
                        },
                    },
                    "required": ["master_password"],
                },
            ),
            Tool(
                name="bw_get_item",
                description=(
                    "Return a single Bitwarden vault item by name. "
                    "Requires bw_unlock to have succeeded earlier in the session."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Item name or id.",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="bw_list_items",
                description=(
                    "List Bitwarden vault items. Optionally filter by folder "
                    "name. Requires bw_unlock to have succeeded earlier."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "Folder name to filter by (free-text search).",
                        },
                    },
                    "required": [],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "bw_unlock":
            return self._unlock(args)
        if tool_name == "bw_get_item":
            return self._get_item(args)
        if tool_name == "bw_list_items":
            return self._list_items(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # bw_unlock
    # ------------------------------------------------------------------

    def _unlock(self, args: dict) -> ToolResult:
        master_password = args.get("master_password")
        if not master_password:
            return ToolResult(
                content="'master_password' is required for bw_unlock",
                is_error=True,
            )
        try:
            proc = self._run_fn(
                ["bw", "unlock", "--raw", "--passwordenv", "BW_PASSWORD"],
                input=master_password,
                env={**os.environ, "BW_PASSWORD": master_password},
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="bw unlock timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"bw unlock failed: {exc}", is_error=True)
        finally:
            # Release the local reference immediately. CPython interning may
            # keep the literal alive elsewhere but at least this scope drops it.
            master_password = None  # noqa: F841

        if proc.returncode != 0:
            return ToolResult(
                content="bw unlock failed (invalid master password or vault locked)",
                is_error=True,
            )

        token = (proc.stdout or "").strip()
        if not token:
            return ToolResult(
                content="bw unlock returned no session token",
                is_error=True,
            )
        self._session_token = token
        return ToolResult(content=json.dumps({"unlocked": True}))

    # ------------------------------------------------------------------
    # bw_get_item
    # ------------------------------------------------------------------

    def _get_item(self, args: dict) -> ToolResult:
        if not self._session_token:
            return ToolResult(
                content="vault is locked — call bw_unlock first",
                is_error=True,
            )
        name = args.get("name")
        if not name:
            return ToolResult(content="'name' is required for bw_get_item", is_error=True)

        argv = ["bw", "get", "item", name]
        return self._run_with_session(argv, success_parser=self._parse_json_object)

    # ------------------------------------------------------------------
    # bw_list_items
    # ------------------------------------------------------------------

    def _list_items(self, args: dict) -> ToolResult:
        if not self._session_token:
            return ToolResult(
                content="vault is locked — call bw_unlock first",
                is_error=True,
            )
        argv = ["bw", "list", "items"]
        if args.get("folder"):
            argv.extend(["--search", args["folder"]])
        return self._run_with_session(argv, success_parser=self._parse_items_list)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_with_session(self, argv: list[str], success_parser: Callable) -> ToolResult:
        env = {**os.environ, "BW_SESSION": self._session_token or ""}
        try:
            proc = self._run_fn(
                argv,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="bw command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"bw command failed: {exc}", is_error=True)

        if proc.returncode != 0:
            return ToolResult(
                content=f"bw command failed: {(proc.stderr or '').strip()}",
                is_error=True,
            )
        return success_parser(proc.stdout or "")

    def _parse_json_object(self, raw: str) -> ToolResult:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ToolResult(content="bw returned non-JSON output", is_error=True)
        return ToolResult(content=json.dumps(data))

    def _parse_items_list(self, raw: str) -> ToolResult:
        try:
            data = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            return ToolResult(content="bw returned non-JSON output", is_error=True)
        return ToolResult(content=json.dumps({"items": data}))


def create() -> BitwardenPlugin:
    return BitwardenPlugin()
