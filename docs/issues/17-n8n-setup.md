## Parent
#1 — PRD: OpenMind v1

## What to build
n8n self-hosted setup and the Felix→n8n MCP bridge. n8n is the integration harness for all cloud services (Google Workspace, Zoom, GitHub API, etc.) — as OpenClaw is for messaging. This slice installs n8n locally, starts it as a background service, and adds an n8n MCP server so Felix can trigger any n8n workflow by name.

## Acceptance criteria
- [ ] n8n runs as a local background service (accessible at localhost)
- [ ] n8n starts automatically with Cerebral or as a system service
- [ ] Felix n8n MCP server exposes: `list_workflows()`, `trigger_workflow(name, data)`, `get_workflow_result(id)`
- [ ] Triggering a test workflow from Felix via voice returns the workflow output
- [ ] n8n data directory is local (no n8n cloud account required)
- [ ] n8n MCP server auto-registers via MCP orchestrator
- [ ] SETUP.md updated with n8n install and start instructions

## Blocked by
- #7 (MCP orchestrator)
