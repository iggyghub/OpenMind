"""
GitHub MCP plugin — Issue #24.

Delegates HTTP to an injected N8nPlugin (same pattern as
GoogleWorkspacePlugin in #20). Tools call out to four pre-built n8n
workflows that hit GitHub's REST API using a stored Personal Access Token.

Tools:
  github_list_issues(repo)              → "Felix GitHub List Issues"
  github_create_issue(repo, title, body?) → "Felix GitHub Create Issue"
  github_list_prs(repo)                 → "Felix GitHub List PRs"
  github_get_notifications()            → "Felix GitHub Notifications"

The n8n GitHub credential needs a personal-access-token with `repo` and
`notifications` scopes — see SETUP.md.
"""
import logging
from typing import Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "github"

# ADR-0005 / Issue #44 — github_list_issues / github_list_prs /
# github_get_notifications read remote state via the local n8n bridge
# (network_egress_local + external_data_read). github_create_issue mutates
# the repo (external_data_write).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "external_data_write",
    "network_egress_local",
})

FetchFn = Callable[..., Awaitable[dict]]

_WORKFLOWS = {
    "github_list_issues": "Felix GitHub List Issues",
    "github_create_issue": "Felix GitHub Create Issue",
    "github_list_prs": "Felix GitHub List PRs",
    "github_get_notifications": "Felix GitHub Notifications",
}

_REQUIRED: dict[str, list[str]] = {
    "github_list_issues": ["repo"],
    "github_create_issue": ["repo", "title"],
    "github_list_prs": ["repo"],
    "github_get_notifications": [],
}


class GithubPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        n8n_plugin=None,
        *,
        fetch_fn: FetchFn | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        if n8n_plugin is not None:
            self._n8n = n8n_plugin
        else:
            from plugins.n8n import N8nPlugin
            self._n8n = N8nPlugin(
                fetch_fn=fetch_fn,
                base_url=base_url,
                api_key=api_key,
            )

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="github_list_issues",
                description="List open issues on a GitHub repo (e.g. 'iggyghub/OpenMind').",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Full repo name in 'owner/name' form.",
                        },
                        "state": {
                            "type": "string",
                            "description": "Issue state: 'open', 'closed', or 'all' (default 'open').",
                        },
                    },
                    "required": ["repo"],
                },
            ),
            Tool(
                name="github_create_issue",
                description="Open a new issue on a GitHub repo.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "owner/name"},
                        "title": {"type": "string", "description": "Issue title"},
                        "body": {
                            "type": "string",
                            "description": "Issue body in Markdown (optional).",
                        },
                    },
                    "required": ["repo", "title"],
                },
            ),
            Tool(
                name="github_list_prs",
                description="List pull requests on a GitHub repo.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "owner/name"},
                        "state": {
                            "type": "string",
                            "description": "PR state: 'open', 'closed', or 'all' (default 'open').",
                        },
                    },
                    "required": ["repo"],
                },
            ),
            Tool(
                name="github_get_notifications",
                description=(
                    "Fetch the authenticated user's GitHub notifications "
                    "(issues, PR reviews, mentions)."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name not in _WORKFLOWS:
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

        for field in _REQUIRED.get(tool_name, []):
            if not args.get(field):
                return ToolResult(
                    content=f"'{field}' is required for {tool_name}",
                    is_error=True,
                )

        workflow_name = _WORKFLOWS[tool_name]
        return await self._n8n.call_tool(
            "trigger_workflow",
            {"name": workflow_name, "data": args},
        )


def create(
    n8n_plugin=None,
    *,
    fetch_fn: FetchFn | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> GithubPlugin:
    return GithubPlugin(
        n8n_plugin=n8n_plugin,
        fetch_fn=fetch_fn,
        base_url=base_url,
        api_key=api_key,
    )
