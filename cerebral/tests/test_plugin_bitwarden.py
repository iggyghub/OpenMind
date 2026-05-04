"""
Bitwarden MCP plugin tests — Issue #26 (Security MCP — HITL).

Tools: bw_unlock(master_password), bw_get_item(name), bw_list_items(folder?).

The plugin shells out to the local `bw` CLI. It is READ-ONLY by design — no
create/update/delete tools are exposed. The master password is passed in by
the caller, used immediately to obtain a session token via `bw unlock --raw`,
and never persisted, never logged, never written to disk. The session token
lives in RAM only and is passed to subsequent `bw` calls via the
BW_SESSION env var.
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
        captured["history"].append({"argv": argv, "kwargs": kwargs})
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    return runner, captured


def _fake_run_responses(responses: list):
    """Sequentially return prepared MagicMocks for each call."""
    captured: dict = {"argv": None, "kwargs": None, "calls": 0, "history": []}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["history"].append({"argv": list(argv), "kwargs": dict(kwargs)})
        idx = captured["calls"]
        captured["calls"] += 1
        spec = responses[idx]
        return MagicMock(
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
            returncode=spec.get("returncode", 0),
        )

    return runner, captured


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools / read-only contract
# ---------------------------------------------------------------------------


class TestListTools:
    def test_create_plugin_named_bitwarden(self):
        from plugins.bitwarden import create

        assert create().name == "bitwarden"

    def test_list_tools_exposes_three(self):
        from plugins.bitwarden import create

        names = {t.name for t in create().list_tools()}
        assert names == {"bw_unlock", "bw_get_item", "bw_list_items"}

    def test_no_write_tools_exposed(self):
        """Read-only contract — no create/update/delete tools."""
        from plugins.bitwarden import create

        names = {t.name for t in create().list_tools()}
        forbidden = {
            "bw_create",
            "bw_create_item",
            "bw_delete",
            "bw_delete_item",
            "bw_edit",
            "bw_edit_item",
            "bw_update",
            "bw_update_item",
        }
        assert names.isdisjoint(forbidden)

    def test_unlock_schema_requires_master_password(self):
        from plugins.bitwarden import create

        tool = next(t for t in create().list_tools() if t.name == "bw_unlock")
        assert "master_password" in tool.schema["required"]

    def test_get_item_schema_requires_name(self):
        from plugins.bitwarden import create

        tool = next(t for t in create().list_tools() if t.name == "bw_get_item")
        assert "name" in tool.schema["required"]

    def test_list_items_schema_no_required(self):
        from plugins.bitwarden import create

        tool = next(t for t in create().list_tools() if t.name == "bw_list_items")
        # folder is optional
        assert tool.schema.get("required", []) == []


# ---------------------------------------------------------------------------
# Cycle 2 — required args
# ---------------------------------------------------------------------------


class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_unlock_missing_password_returns_error(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, captured = _fake_run()
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_unlock", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_get_item_missing_name_returns_error(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, captured = _fake_run()
        plugin = BitwardenPlugin(run_fn=run_fn)
        # unlock first so we have a session
        unlock_run, _ = _fake_run(stdout="session-token-abc\n")
        plugin = BitwardenPlugin(run_fn=unlock_run)
        await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        # now swap in capturing run
        plugin._run_fn = run_fn
        result = await plugin.call_tool("bw_get_item", {})
        assert result.is_error
        assert captured["calls"] == 0


# ---------------------------------------------------------------------------
# Cycle 3 — bw_unlock argv shape and session capture
# ---------------------------------------------------------------------------


class TestUnlock:
    @pytest.mark.asyncio
    async def test_unlock_calls_bw_unlock_raw(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, captured = _fake_run(stdout="session-token-xyz\n")
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_unlock", {"master_password": "hunter2"})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "bw"
        assert "unlock" in argv
        assert "--raw" in argv

    @pytest.mark.asyncio
    async def test_unlock_passes_password_via_stdin_or_arg(self):
        """Master password must be passed to bw, but our test doesn't care exactly how."""
        from plugins.bitwarden import BitwardenPlugin

        run_fn, captured = _fake_run(stdout="session-token-xyz\n")
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "hunter2"})
        # Should be passed somehow — either argv or stdin.
        argv = captured["argv"]
        kwargs = captured["kwargs"]
        passed = (
            "hunter2" in argv
            or kwargs.get("input") == "hunter2"
            or kwargs.get("input") == "hunter2\n"
        )
        assert passed

    @pytest.mark.asyncio
    async def test_unlock_response_does_not_leak_password(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, _ = _fake_run(stdout="session-token-xyz\n")
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_unlock", {"master_password": "supersecret"})
        assert "supersecret" not in result.content

    @pytest.mark.asyncio
    async def test_unlock_response_does_not_leak_session_token(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, _ = _fake_run(stdout="session-token-xyz\n")
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        # the session token itself is sensitive — never echo it back
        assert "session-token-xyz" not in result.content

    @pytest.mark.asyncio
    async def test_unlock_failure_returns_error(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, _ = _fake_run(stderr="Invalid master password.", returncode=1)
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_unlock", {"master_password": "wrong"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unlock_run_fn_raises_returns_error(self):
        from plugins.bitwarden import BitwardenPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("bw not on PATH")

        plugin = BitwardenPlugin(run_fn=boom)
        result = await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — bw_get_item / bw_list_items use the captured session
# ---------------------------------------------------------------------------


class TestGetItem:
    @pytest.mark.asyncio
    async def test_get_item_requires_unlock_first(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, captured = _fake_run()
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_get_item", {"name": "github"})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_get_item_uses_session_after_unlock(self):
        from plugins.bitwarden import BitwardenPlugin

        responses = [
            {"stdout": "session-abc\n"},
            {"stdout": json.dumps({"name": "github", "login": {"username": "iggy"}})},
        ]
        run_fn, captured = _fake_run_responses(responses)
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        result = await plugin.call_tool("bw_get_item", {"name": "github"})
        assert not result.is_error
        get_call = captured["history"][1]
        argv = get_call["argv"]
        assert argv[0] == "bw"
        assert "get" in argv
        assert "item" in argv
        assert "github" in argv
        # Session is passed via env var BW_SESSION
        env = get_call["kwargs"].get("env", {})
        assert env.get("BW_SESSION") == "session-abc"

    @pytest.mark.asyncio
    async def test_get_item_returns_item_payload(self):
        from plugins.bitwarden import BitwardenPlugin

        item_payload = {"name": "github", "login": {"username": "iggy", "password": "p"}}
        responses = [
            {"stdout": "session-abc\n"},
            {"stdout": json.dumps(item_payload)},
        ]
        run_fn, _ = _fake_run_responses(responses)
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        result = await plugin.call_tool("bw_get_item", {"name": "github"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["name"] == "github"

    @pytest.mark.asyncio
    async def test_get_item_unknown_returns_error(self):
        from plugins.bitwarden import BitwardenPlugin

        responses = [
            {"stdout": "session-abc\n"},
            {"stderr": "Not found.", "returncode": 1},
        ]
        run_fn, _ = _fake_run_responses(responses)
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        result = await plugin.call_tool("bw_get_item", {"name": "nope"})
        assert result.is_error


class TestListItems:
    @pytest.mark.asyncio
    async def test_list_items_requires_unlock(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, captured = _fake_run()
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_list_items", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_list_items_after_unlock_returns_items(self):
        from plugins.bitwarden import BitwardenPlugin

        items = [{"name": "github"}, {"name": "aws"}]
        responses = [
            {"stdout": "session-abc\n"},
            {"stdout": json.dumps(items)},
        ]
        run_fn, captured = _fake_run_responses(responses)
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        result = await plugin.call_tool("bw_list_items", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["items"] == items
        argv = captured["history"][1]["argv"]
        assert argv[0] == "bw"
        assert "list" in argv
        assert "items" in argv

    @pytest.mark.asyncio
    async def test_list_items_with_folder_filter(self):
        from plugins.bitwarden import BitwardenPlugin

        responses = [
            {"stdout": "session-abc\n"},
            {"stdout": json.dumps([])},
        ]
        run_fn, captured = _fake_run_responses(responses)
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        await plugin.call_tool("bw_list_items", {"folder": "Personal"})
        argv = captured["history"][1]["argv"]
        # folder passed somehow — either as --folderid or --search
        joined = " ".join(argv)
        assert "Personal" in joined or "--folderid" in joined or "--search" in joined


# ---------------------------------------------------------------------------
# Cycle 5 — unknown tool
# ---------------------------------------------------------------------------


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, _ = _fake_run()
        plugin = BitwardenPlugin(run_fn=run_fn)
        result = await plugin.call_tool("bw_nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — secrets hygiene
# ---------------------------------------------------------------------------


class TestSecretsHygiene:
    @pytest.mark.asyncio
    async def test_master_password_not_stored_on_plugin(self):
        from plugins.bitwarden import BitwardenPlugin

        run_fn, _ = _fake_run(stdout="session-abc\n")
        plugin = BitwardenPlugin(run_fn=run_fn)
        await plugin.call_tool("bw_unlock", {"master_password": "secret"})
        # Walk the plugin's __dict__ — no string attribute should hold the password
        for value in plugin.__dict__.values():
            if isinstance(value, str):
                assert "secret" not in value

    @pytest.mark.asyncio
    async def test_session_token_not_logged_in_results(self):
        from plugins.bitwarden import BitwardenPlugin

        item_payload = {"name": "x", "login": {"password": "p"}}
        responses = [
            {"stdout": "secret-session-token\n"},
            {"stdout": json.dumps(item_payload)},
        ]
        run_fn, _ = _fake_run_responses(responses)
        plugin = BitwardenPlugin(run_fn=run_fn)
        unlock_result = await plugin.call_tool("bw_unlock", {"master_password": "pw"})
        get_result = await plugin.call_tool("bw_get_item", {"name": "x"})
        # Session token should not leak into either result
        assert "secret-session-token" not in unlock_result.content
        assert "secret-session-token" not in get_result.content
