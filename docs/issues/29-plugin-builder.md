## Parent
#1 — PRD: OpenMind v1

## What to build
The plugin builder — the growth loop in code. User describes a missing capability in plain language. The LLM generates a complete Python MCP server, installs dependencies, runs a smoke test, and registers it with the MCP orchestrator. Generated code lands in /plugins for inspection. Felix can extend itself.

## Acceptance criteria
- [ ] User can say "Felix, I need you to be able to X" to trigger the plugin builder
- [ ] LLM generates a complete, runnable Python MCP server for the described capability
- [ ] Generated server saved to /plugins/<name>/ with server.py and README.md
- [ ] Plugin builder auto-installs any pip dependencies declared by the generated server
- [ ] A basic tool-call smoke test runs before registration
- [ ] On passing smoke test, server registers with MCP orchestrator immediately
- [ ] On failing smoke test, Felix reports the error and does not register the broken server
- [ ] Demo: describe a new capability by voice → Felix builds it → it works in the same session
- [ ] Generated servers survive Cerebral restarts (auto-discovered from /plugins)

## Blocked by
- #6 (model router)
- #7 (MCP orchestrator)
