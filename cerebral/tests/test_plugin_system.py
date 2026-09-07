import asyncio

from plugins.system import REQUIRED_CAPABILITIES, SystemPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_get_volume_happy_path():
    import subprocess

    plugin = SystemPlugin(
        run_fn=lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="42\n", stderr="")
    )
    result = asyncio.run(plugin.call_tool("get_volume", {}))
    assert result.is_error is False
