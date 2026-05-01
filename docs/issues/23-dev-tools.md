## Parent
#1 — PRD: OpenMind v1

## What to build
Developer tool MCP servers: Git (CLI: status/commit/push/pull/diff/log/branch), GitHub/GitLab (API via n8n: issues/PRs/notifications), Docker (CLI: list/start/stop/build), Package Managers (npm/pip/winget), SSH (connect to remote machines, run commands), HTTP Client (API requests, webhooks, endpoint testing).

## Acceptance criteria
- [ ] Git: all standard operations available as MCP tools against a specified repo path
- [ ] GitHub: list issues, create issue, list PRs, get notifications via n8n workflow
- [ ] Docker: list containers, start/stop container, list images, run build
- [ ] Package managers: install, update, search for npm, pip, and winget
- [ ] SSH: connect to a configured host, run a command, return output
- [ ] HTTP Client: GET/POST/PUT/DELETE with headers and body, return status and response
- [ ] Demo: "Felix, what is the git status of my current repo?" → spoken summary

## Blocked by
- #7 (MCP orchestrator)
- #18 (n8n setup)
