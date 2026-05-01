## Parent
#1 — PRD: OpenMind v1

## What to build
Browser MCP server using OpenClaw's bundled Playwright: web search, navigate to URL, extract readable content via Mozilla Readability (also bundled), and summarise. Felix can answer questions requiring live web information.

## Acceptance criteria
- [ ] Web search returns top results (title, URL, snippet) for a given query
- [ ] Navigate to URL extracts main readable content headlessly
- [ ] Content is summarised by the LLM and spoken as a response
- [ ] PDF URLs handled via OpenClaw's bundled PDF.js
- [ ] No visible browser window opened
- [ ] Demo: "Felix, what is the latest Python version?" → Felix searches, reads, speaks the answer
- [ ] Auto-registers via MCP orchestrator

## Blocked by
- #7 (MCP orchestrator)
