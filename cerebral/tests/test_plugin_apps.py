"""Tests for plugins/apps.py — the launch path must never touch a shell (#369)."""
import json

from plugins.apps import AppsPlugin


class _FakeProc:
    pid = 4321


async def test_launch_app_uses_argv_no_shell():
    """launch_app passes a single-element argv and no shell kwarg (#369).

    shell=True let LLM-supplied strings like ``cmd /c ...`` reach cmd.exe
    under the silently-granted device_control capability, bypassing the
    shell_exec deny-default and the ADR-0010 sandbox.
    """
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProc()

    plugin = AppsPlugin(popen_fn=fake_popen)
    result = await plugin.call_tool("launch_app", {"app": "notepad & evil.exe"})

    assert json.loads(result.content) == {"pid": 4321, "ok": True}
    (args, kwargs), = calls
    assert args == (["notepad & evil.exe"],)  # one argv element, metachars inert
    assert "shell" not in kwargs


async def test_launch_app_missing_app_is_error():
    """With no shell, a missing executable surfaces as a clean tool error."""

    def fake_popen(*args, **kwargs):
        raise FileNotFoundError

    plugin = AppsPlugin(popen_fn=fake_popen)
    result = await plugin.call_tool("launch_app", {"app": "no-such-app"})

    assert result.is_error
    assert "no-such-app" in result.content
