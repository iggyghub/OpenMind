"""
Docker MCP plugin tests — Issue #24.

Tools: docker_list_containers, docker_start_container,
docker_stop_container, docker_list_images, docker_build.
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
# Cycle 1 — list_tools exposes all five docker tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_five(self):
        from plugins.docker import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "docker_list_containers",
            "docker_start_container",
            "docker_stop_container",
            "docker_list_images",
            "docker_build",
        }

    def test_all_tools_have_correct_plugin_name(self):
        from plugins.docker import create

        for tool in create().list_tools():
            assert tool.plugin == "docker"

    def test_create_plugin_named_docker(self):
        from plugins.docker import create

        assert create().name == "docker"


# ---------------------------------------------------------------------------
# Cycle 2 — list/build subcommands shell out via run_fn
# ---------------------------------------------------------------------------

class TestListContainers:
    @pytest.mark.asyncio
    async def test_list_containers_calls_docker_ps(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run(stdout="[]")
        plugin = DockerPlugin(run_fn=run_fn)

        result = await plugin.call_tool("docker_list_containers", {})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "docker"
        assert "ps" in argv


class TestListImages:
    @pytest.mark.asyncio
    async def test_list_images_calls_docker_images(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run(stdout="")
        plugin = DockerPlugin(run_fn=run_fn)

        result = await plugin.call_tool("docker_list_images", {})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "docker"
        assert "images" in argv


# ---------------------------------------------------------------------------
# Cycle 3 — start/stop require name_or_id
# ---------------------------------------------------------------------------

class TestStartStopContainer:
    @pytest.mark.asyncio
    async def test_start_missing_target_returns_error(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("docker_start_container", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_stop_missing_target_returns_error(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("docker_stop_container", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_start_passes_target_to_docker(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "docker_start_container", {"name_or_id": "myapp"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert "start" in argv
        assert "myapp" in argv

    @pytest.mark.asyncio
    async def test_stop_passes_target_to_docker(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "docker_stop_container", {"name_or_id": "container123"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert "stop" in argv
        assert "container123" in argv


# ---------------------------------------------------------------------------
# Cycle 4 — docker_build requires path; tag is optional
# ---------------------------------------------------------------------------

class TestDockerBuild:
    @pytest.mark.asyncio
    async def test_build_missing_path_returns_error(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("docker_build", {})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_build_passes_path(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("docker_build", {"path": "."})
        assert not result.is_error
        argv = captured["argv"]
        assert "build" in argv
        assert "." in argv

    @pytest.mark.asyncio
    async def test_build_with_tag_passes_t_flag(self):
        from plugins.docker import DockerPlugin

        run_fn, captured = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "docker_build", {"path": ".", "tag": "myimage:latest"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert "-t" in argv
        assert "myimage:latest" in argv


# ---------------------------------------------------------------------------
# Cycle 5 — error paths
# ---------------------------------------------------------------------------

class TestErrors:
    @pytest.mark.asyncio
    async def test_non_zero_exit_is_error(self):
        from plugins.docker import DockerPlugin

        run_fn, _ = _fake_run(stderr="docker: command not found", returncode=1)
        plugin = DockerPlugin(run_fn=run_fn)

        result = await plugin.call_tool("docker_list_containers", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_fn_raises_returns_error(self):
        from plugins.docker import DockerPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("docker not on PATH")

        plugin = DockerPlugin(run_fn=boom)
        result = await plugin.call_tool("docker_list_containers", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.docker import DockerPlugin

        run_fn, _ = _fake_run()
        plugin = DockerPlugin(run_fn=run_fn)
        result = await plugin.call_tool("docker_nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — success path returns stdout/stderr/exit_code dict
# ---------------------------------------------------------------------------

class TestSuccessShape:
    @pytest.mark.asyncio
    async def test_success_returns_dict(self):
        from plugins.docker import DockerPlugin

        run_fn, _ = _fake_run(stdout="CONTAINER ID  IMAGE\n", returncode=0)
        plugin = DockerPlugin(run_fn=run_fn)

        result = await plugin.call_tool("docker_list_containers", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "stdout" in data
        assert data["exit_code"] == 0
