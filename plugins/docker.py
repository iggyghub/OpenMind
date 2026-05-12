"""
Docker MCP plugin — Issue #24.

Tools: docker_list_containers, docker_start_container, docker_stop_container,
docker_list_images, docker_build.

Shells out to the local `docker` binary via an injectable run_fn.
Requires Docker installed and reachable on PATH.
"""
import json
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "docker"

# ADR-0005 / Issue #44 — docker_list_containers / docker_start / docker_stop
# / docker_list_images / docker_build all manage local container state via
# the docker CLI. The argv list is constructed from a closed set of
# subcommands; the user controls only target names and build paths.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"device_control"})


class DockerPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="docker_list_containers",
                description="List running containers (`docker ps`).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "all": {
                            "type": "boolean",
                            "description": "Include stopped containers (default false)",
                        },
                    },
                },
            ),
            Tool(
                name="docker_start_container",
                description="Start a container by name or id.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name_or_id": {"type": "string", "description": "Container name or id"},
                    },
                    "required": ["name_or_id"],
                },
            ),
            Tool(
                name="docker_stop_container",
                description="Stop a running container by name or id.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name_or_id": {"type": "string", "description": "Container name or id"},
                    },
                    "required": ["name_or_id"],
                },
            ),
            Tool(
                name="docker_list_images",
                description="List local images (`docker images`).",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="docker_build",
                description="Build an image from a Dockerfile in the given path. Optional tag.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Build context path"},
                        "tag": {"type": "string", "description": "Image tag (optional)"},
                    },
                    "required": ["path"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "docker_list_containers":
            argv = ["docker", "ps"]
            if args.get("all"):
                argv.append("-a")
            return self._run(argv)
        if tool_name == "docker_list_images":
            return self._run(["docker", "images"])
        if tool_name == "docker_start_container":
            target = args.get("name_or_id")
            if not target:
                return ToolResult(
                    content="'name_or_id' is required for docker_start_container",
                    is_error=True,
                )
            return self._run(["docker", "start", target])
        if tool_name == "docker_stop_container":
            target = args.get("name_or_id")
            if not target:
                return ToolResult(
                    content="'name_or_id' is required for docker_stop_container",
                    is_error=True,
                )
            return self._run(["docker", "stop", target])
        if tool_name == "docker_build":
            path = args.get("path")
            if not path:
                return ToolResult(
                    content="'path' is required for docker_build",
                    is_error=True,
                )
            argv = ["docker", "build"]
            if args.get("tag"):
                argv.extend(["-t", args["tag"]])
            argv.append(path)
            return self._run(argv)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _run(self, argv: list[str]) -> ToolResult:
        try:
            proc = self._run_fn(
                argv,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="docker command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"docker command failed: {exc}", is_error=True)

        payload = json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
        return ToolResult(content=payload, is_error=proc.returncode != 0)


def create() -> DockerPlugin:
    return DockerPlugin()
