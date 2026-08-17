---
name: adr-amend
description: Appends a house-style amendment to an existing ADR or drafts a new numbered one, matching the repo's documentation standards; trigger phrases: "amend the ADR", "add an ADR amendment", "write an ADR", "/adr-amend".
---

Amendments are how a decision evolves in this repo -- an ADR is never silently rewritten, the history stays visible.

### Amendment block skeleton

Append this exact structure to an existing ADR file:

```markdown
## Amendment (YYYY-MM-DD) -- <short title>

**Context** -- what changed since the original decision that forces a revisit.
**Decision** -- the new call, stated plainly.
**Considered and rejected** -- the alternatives, and why each lost.
**Consequences** -- what this now obliges or forbids.
```

### New ADR skeleton

When a decision is genuinely new rather than an evolution, create a numbered file `docs/adr/NNNN-kebab-title.md`. Pick `NNNN` by listing `docs/adr/` and taking one higher than the highest existing number -- never a hardcoded number, and never reuse one (the highest was 0024 as of 2026-08-17, so a new ADR would be 0025). Structure the file as:

- Title line
- Date
- Status
- Relates
- Context
- Decisions
- Consequences

### Cross-reference discipline

Always name the ADR and, where relevant, the specific amendment being extended or superseded. A reader must be able to follow the chain without guessing.

### Guard: do not silently change an invariant

Some things are standing invariants across the repo -- the 16-class capability vocabulary is the main one. If an amendment WOULD change an invariant, it must say so explicitly as a deliberate vocabulary change, never slip it through as a detail. Conversely, when an amendment leaves the invariants untouched, re-assert that plainly (for example "the 16-class capability vocabulary is unchanged") so a reader knows it was considered.

### Before writing

Read the ADR being amended in full first, and check whether a later amendment already covers the same ground.
