## Parent
#1 — PRD: OpenMind v1

## What to build
Three time and capture MCP servers: Clock (timers, alarms, reminders, world time), Scheduler (local calendar events and recurring tasks), Notes (quick-capture searchable local notes stored as markdown files).

## Acceptance criteria
- [ ] Clock: set timer, set alarm, set one-off reminder (fires via OS notification), query time in any timezone
- [ ] Scheduler: create/list/update/delete local events in SQLite; recurring event support (daily/weekly/monthly)
- [ ] Notes: create note, search by keyword, list recent, delete — stored as markdown in a configurable directory
- [ ] Demo: "Felix, remind me in 30 minutes to check the build" → reminder fires via OS notification
- [ ] All three auto-register via MCP orchestrator

## Blocked by
- #7 (MCP orchestrator)
