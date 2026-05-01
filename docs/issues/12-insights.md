## Parent
#1 — PRD: OpenMind v1

## What to build
Passive preference learning and Insights view. Every queue approval and dismissal is a signal. The insights engine detects patterns and stores them as profile-scoped insight records. The Insights view in the tray shows what Felix has learned, with edit/delete/pin controls.

## Acceptance criteria
- [ ] Every approve and dismiss is recorded as a signal with timestamp and action type
- [ ] After repeated patterns (configurable threshold), an insight record is created
- [ ] Insights view is accessible from the tray menu
- [ ] Each insight shows: what was learned, when, and an example trigger
- [ ] User can delete any insight (stops that behaviour adjustment)
- [ ] User can pin an insight (locks it, prevents auto-removal)
- [ ] User can edit the description of an insight

## Blocked by
- #11 (passive 5W1H)
- #12 (memory manager)
