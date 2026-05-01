## Parent
#1 — PRD: OpenMind v1

## What to build
Tray app queue pulldown: the system tray icon expands to show pending candidate actions. Each action can be approved (Felix executes it) or dismissed (removed silently). Tray badge shows pending count.

## Acceptance criteria
- [ ] Tray icon badge shows count of pending queue items
- [ ] Clicking the tray icon opens a pulldown listing all pending actions with title and summary
- [ ] Each item has Approve and Dismiss buttons
- [ ] Approving triggers execution via the MCP orchestrator and removes the item from the queue
- [ ] Dismissing removes the item without executing
- [ ] Queue state persists across tray open/close
- [ ] Empty queue shows "No pending actions" state

## Blocked by
- #2 (project scaffold)
- #7 (MCP orchestrator)
