## Parent
#1 — PRD: OpenMind v1

## What to build
Google Workspace MCP servers via n8n: Gmail, Calendar, Drive, Docs, Sheets, Slides, Contacts, Maps, and Tasks. Each is an n8n workflow triggered through the Felix n8n MCP bridge. The MCP interface is service-agnostic so local OSS fallbacks can swap in transparently.

## Acceptance criteria
- [ ] Gmail: read inbox, search, send, label, mark read/unread
- [ ] Calendar: list events, create, update, check availability
- [ ] Drive: search files, upload, download, list folder contents
- [ ] Docs: read content, create document, append text
- [ ] Sheets: read range, write range, append row, create spreadsheet
- [ ] Slides: read slide content, create presentation
- [ ] Contacts: search, get contact details
- [ ] Maps: directions, place search, travel time estimate
- [ ] Tasks: list, create, mark complete
- [ ] All use the same abstract MCP interface (swap-ready for OSS fallbacks)
- [ ] Demo: "Felix, what emails do I have from John this week?" → spoken summary

## Blocked by
- #19 (n8n credentials)
