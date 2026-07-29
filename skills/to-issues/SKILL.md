---
name: to-issues
description: Break a plan, spec, or PRD into independently-grabbable issues on the project issue tracker using tracer-bullet vertical slices. Use when the user wants a plan converted into tickets, asks to "break this down into issues", or hands over a design to turn into implementation work.
kind: procedure
tools: [github_list_issues, github_create_issue]
---

# To issues

Break a plan into independently-grabbable issues using vertical slices
(tracer bullets), then publish them to the project's issue tracker.

The issue tracker for this project is GitHub Issues; the triage label
vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`) is documented in `docs/agents/triage-labels.md`.

## Process

### 1. Gather context

Work from whatever plan or spec is already in the conversation. If the user
references an existing issue by number or URL, use `github_list_issues` to
fetch and read its full body first.

### 2. Explore the codebase (optional)

If you haven't already, explore the relevant code so slice titles and
descriptions use the project's own domain vocabulary (CONTEXT.md) and respect
the ADRs touching that area.

### 3. Draft vertical slices

Break the plan into tracer-bullet issues: each is a thin vertical slice that
cuts through every layer end-to-end (schema, backend, UI, tests) rather than
a horizontal slice of a single layer.

- Each slice delivers a narrow but complete path, demoable or verifiable on
  its own.
- Prefer many thin slices over a few thick ones.
- Mark each slice HITL (needs human interaction -- an architectural decision
  or design review) or AFK (can be implemented and merged unattended).
  Prefer AFK where possible.

### 4. Quiz the user

Present the breakdown as a numbered list. For each slice show: title, type
(HITL/AFK), what blocks it, and which requirements it covers. Ask whether the
granularity is right, whether dependencies are correct, and whether any
slices should merge or split. Iterate until the user approves.

### 5. Publish

For each approved slice, call `github_create_issue` with the `needs-triage`
label so it enters the normal triage flow. Publish in dependency order
(blockers first) so later issues can reference real issue numbers in their
"Blocked by" section. Use this body template:

```
## Parent

Reference to the parent issue (omit if there isn't one).

## What to build

The end-to-end behavior this slice delivers -- not a layer-by-layer
implementation list.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

- Reference to the blocking issue, or "None - can start immediately"
```

Do not close or modify any parent issue.
