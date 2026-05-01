## Parent
#1 — PRD: OpenMind v1

## What to build
Local OSS fallbacks for every Google Workspace service, activated automatically when offline. Grist replaces Sheets, IMAP/SMTP replaces Gmail, Nextcloud replaces Drive, LibreOffice replaces Docs/Slides, OpenStreetMap replaces Maps. The MCP interface is identical — Felix's behaviour does not change based on which backend is active.

## Acceptance criteria
- [ ] Grist (local) routes for Sheets MCP when Google is unreachable
- [ ] IMAP/SMTP routes for Gmail MCP when Google is unreachable
- [ ] OpenStreetMap API used for Maps MCP when offline
- [ ] LibreOffice CLI handles Docs and Slides read/create when offline
- [ ] Nextcloud (if installed) handles Drive operations when offline
- [ ] Offline detection is automatic — no manual switching required
- [ ] Same Felix voice commands produce equivalent results whether online or offline
- [ ] Demo: disable internet, ask for recent emails → IMAP response delivered

## Blocked by
- #20 (Google Workspace MCP)
