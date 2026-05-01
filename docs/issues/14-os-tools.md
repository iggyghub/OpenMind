## Parent
#1 — PRD: OpenMind v1

## What to build
Five OS-level MCP servers: Files (create/read/move/delete/search), Shell (run terminal commands), System (volume/brightness/WiFi/screenshots/power), Apps (launch/switch/close), Clipboard (read/write/history). Full local machine access.

## Acceptance criteria
- [ ] Files: create, read, move, delete, and fuzzy-search files and folders
- [ ] Shell: run arbitrary terminal commands, capture stdout/stderr, return exit code
- [ ] System: get/set volume, brightness, take screenshot to file, check WiFi, initiate shutdown/restart
- [ ] Apps: list running apps, launch by name, bring to foreground, close
- [ ] Clipboard: read current content, write text, list recent history
- [ ] All five auto-register on startup via MCP orchestrator
- [ ] Demo: "Felix, take a screenshot and save it to my Desktop" executes end-to-end

## Blocked by
- #7 (MCP orchestrator)
