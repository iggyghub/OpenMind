## Parent
#1 — PRD: OpenMind v1

## What to build
Profile manager: create, load, update, and delete user profiles in a local SQLite database. Each profile stores the user's name, preferred wake name, pronunciation guide, preferred voice ID, and connected account references. At launch, Felix auto-loads the last-used profile; a creation prompt appears on first run. The active profile name appears in the tray menu.

## Acceptance criteria
- [ ] Profiles persist in SQLite across process restarts
- [ ] On first launch, a profile creation prompt appears before Felix starts
- [ ] On subsequent launches, the last-used profile is auto-loaded
- [ ] Tray menu shows the active profile name
- [ ] Profile can be switched from the tray menu without restarting
- [ ] Profile stores: name, wake name, pronunciation guide, voice ID, connected accounts (empty list initially)
- [ ] Deleting a profile removes its SQLite record

## Blocked by
- #2 (project scaffold)
