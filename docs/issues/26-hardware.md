## Parent
#1 — PRD: OpenMind v1

## What to build
Hardware MCP servers: Printer/Scanner (print a file, check status, scan document to file) and Game Launcher (Steam: list installed games, launch by name or app ID, check if a game is running).

## Acceptance criteria
- [ ] Printer: print a file to default or named printer, get queue status, list available printers
- [ ] Scanner: initiate scan to a specified output path and format (PDF or PNG)
- [ ] Steam: list installed games, launch by name or app ID, check if a game is running
- [ ] Demo: "Felix, launch Cyberpunk 2077" → Steam opens the game
- [ ] Demo: "Felix, scan this document and save as PDF to my Desktop" → scan initiates
- [ ] Both auto-register via MCP orchestrator
- [ ] Graceful error if hardware is not connected

## Blocked by
- #7 (MCP orchestrator)
