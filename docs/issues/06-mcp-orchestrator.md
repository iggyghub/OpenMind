## Parent
#1 — PRD: OpenMind v1

## What to build
MCP orchestrator: plugin registry that discovers, loads, and manages MCP servers. Routes tool calls from the LLM to the correct server. This is the central nerve all downstream MCP server slices depend on.

## Acceptance criteria
- [ ] MCP servers in /plugins are auto-discovered and registered on startup
- [ ] `list_tools()` returns a unified list of all tools across all registered servers
- [ ] `call_tool(name, args)` routes to the correct MCP server and returns the result
- [ ] Registering a new server at runtime makes its tools immediately available without restart
- [ ] Unregistering a server removes its tools from the list
- [ ] Unrecognised tool names return a structured error, not a crash
- [ ] Tool list is exposed to the model router so the LLM can select tools

## Blocked by
- #6 (model router)
