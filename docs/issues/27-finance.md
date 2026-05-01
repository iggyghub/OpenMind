## Parent
#1 — PRD: OpenMind v1

## What to build
Finance MCP server: OCR-powered invoice and receipt processing. Felix accepts an image or scanned PDF, extracts structured data (vendor, date, amount, line items) using a local OCR model, and appends a row to a configured Google Sheet or local Grist table.

## Acceptance criteria
- [ ] Accepts image path or scanned PDF path as input
- [ ] OCR extracts: vendor name, date, total amount, currency, and line items where present
- [ ] Extracted data is mapped to a configurable column schema in Google Sheets or Grist
- [ ] A new row is appended to the configured sheet on each successful extraction
- [ ] Extraction confidence is reported — low-confidence fields are flagged for review
- [ ] Demo: "Felix, add this receipt to my expenses" (with image path) → row appears in expense sheet
- [ ] Works with Grist fallback when Google Sheets is unavailable

## Blocked by
- #20 (Google Workspace MCP)
- #27 (Hardware MCP — scanner input)
