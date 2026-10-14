# Help Tab Maintenance Guide

## What it is
The Help tab has two halves:
- The Guide: hand-written content in `tray/lib/help-content.js`.
- The Capabilities sub-tab: generated live from the `plugins:list` websocket snapshot. Never edited by hand.

## Adding or editing a topic
Append a topic object to `topics` in `tray/lib/help-content.js`. Array order determines UI order. The section list is derived from the `section` field in first-appearance order.

Topic shape:
```
{
  id: 'unique-identifier',
  section: 'Section Name',
  title: 'Topic Title',
  summary: 'One-line summary.',
  body: ['A paragraph.', '- a bullet', '- another bullet'],
  see_also: ['another-topic-id'] // optional
}
```

## Body mini-format
Strings are single-quoted JavaScript: escape apostrophes as `'` (an unescaped `Felix's` breaks the whole file, and the Help tab with it).

A body string starting with `- ` is a bullet. Consecutive bullets collapse into one list. Anything else is a paragraph. No markdown beyond that.

## What must never go in the Guide
- Individual tool names (they go stale; the Capabilities tab is the live index).
- Secrets or credential values.
- Anything about a subsystem that is planned rather than built.

## Sources of truth
- `CONTEXT.md` for domain language and architecture.
- `docs/adr/*.md` for decisions.
- Where the Guide and an ADR disagree, the ADR wins and the Guide is stale. Update the Guide accordingly.

## How to check the work
Run `cd tray; npm test`. This executes:
- `help-content.test.js` (checks unique ids, every `see_also` resolves, every topic renders).
- `help-panel.test.js` and `help-capabilities.test.js`.

## When to update
- After an ADR lands or is amended.
- After a new subsystem ships.
- Whenever a topic's described behaviour is found to be wrong.
The Guide is not auto-generated and will silently rot if nobody updates it. Every feature change updates its Help topic in the same PR.

## Where the code is
- `tray/lib/help-content.js` (content)
- `tray/lib/help-panel.js` (rendering)
- `tray/windows/main.html` (pane + wiring)
- `tray/tests/help-panel.test.js`, `help-capabilities.test.js` and `help-content.test.js` (checks)
