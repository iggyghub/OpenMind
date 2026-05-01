## Parent
#1 — PRD: OpenMind v1

## What to build
The bare-bones skeleton that proves the two-process architecture works end-to-end. A Python Cerebral backend process and a Node.js tray frontend process start together, communicate over a local WebSocket IPC bridge, and a system tray icon confirms Felix is running. No AI, no audio — just the plumbing everything else will run on.

## Acceptance criteria
- [ ] `python cerebral/main.py` starts the Cerebral backend without error
- [ ] `npm start` starts the Node.js tray process
- [ ] System tray icon appears showing Felix is running
- [ ] Cerebral can send an event over the IPC bridge and the tray frontend receives and logs it
- [ ] Both processes shut down cleanly when the tray icon is quit
- [ ] SETUP.md documents the dev environment start sequence

## Blocked by
None — can start immediately
