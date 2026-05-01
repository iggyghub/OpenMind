## Parent
#1 — PRD: OpenMind v1

## What to build
Security MCP servers: Bitwarden (read-only access to local CLI vault — retrieve by item name, vault unlock prompted once per session), VPN (connect/disconnect/status for system VPN), Network Scanner (list local network devices, check ports, ping hosts).

## Acceptance criteria
- [ ] Bitwarden: unlock vault with master password (prompted once per session, never stored), retrieve item by name, list items by folder
- [ ] Bitwarden: read-only — no create, update, or delete operations exposed
- [ ] VPN: connect to configured VPN profile, disconnect, get connection status and current IP
- [ ] Network Scanner: list devices on local network with IP and hostname, ping a host, check if a port is open
- [ ] Demo: "Felix, am I connected to VPN?" → spoken status response
- [ ] All three auto-register via MCP orchestrator

## Blocked by
- #7 (MCP orchestrator)
