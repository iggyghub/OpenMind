## Parent
#1 — PRD: OpenMind v1

## What to build
One-time credential setup in n8n for all cloud services: Google OAuth (covering all Workspace services), Zoom OAuth, and other services requiring auth. After this slice, all downstream cloud integration slices are AFK — credentials are stored in n8n's encrypted local vault and reused by workflows.

## Acceptance criteria
- [ ] Google OAuth credential configured in n8n (covers Gmail, Calendar, Drive, Docs, Sheets, Slides, Contacts, Maps, Tasks)
- [ ] Zoom OAuth credential configured in n8n
- [ ] A test n8n workflow successfully calls the Google Calendar API using the stored credential
- [ ] Credentials stored in n8n's local encrypted credential vault
- [ ] No credentials stored in the Felix codebase or environment files
- [ ] SETUP.md documents the one-time credential setup steps

## Blocked by
- #18 (n8n setup)
