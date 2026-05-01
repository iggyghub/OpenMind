## Parent
#1 — PRD: OpenMind v1

## What to build
Communication MCP servers: Zoom and Google Meet join/schedule via n8n workflows, and phone calls via OpenClaw channels. Felix can join a calendar meeting, schedule a new one, or initiate a call by voice.

## Acceptance criteria
- [ ] Zoom: join meeting by URL or ID, schedule meeting via n8n, list upcoming Zoom meetings
- [ ] Google Meet: join via URL, schedule via Google Calendar integration, get meeting link for an event
- [ ] Phone calls: initiate a call to a contact via an OpenClaw-supported channel
- [ ] Demo: "Felix, join my 3pm Zoom call" → Zoom opens and joins
- [ ] Demo: "Felix, schedule a Zoom call with John tomorrow at 2pm" → calendar event created with Zoom link
- [ ] All capabilities auto-register as MCP tools

## Blocked by
- #19 (n8n credentials)
- #22 (OpenClaw channel bridge)
