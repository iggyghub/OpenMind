# ADR-0035: The registry widget, and a safe `skill_update`

**Date:** 2026-09-06
**Status:** Accepted (grill session)
**Relates:** ADR-0034 (verification contract -- this is where `VerifyResult`
surfaces visually), ADR-0031 (interface guidelines -- the same "unify the
rules, not the rendering" move, one layer deeper), ADR-0012 (panel spec /
panel vocabulary), ADR-0014 (Skills -- provenance = repo + commit SHA), the
Main window's Code load entry in CONTEXT.md (the destructive-reading-must-be-
opt-in precedent this borrows).

## Context

Three mechanisms already have a browsing UI -- Plugins panel, the Skills
sub-tab (under Harness, S5 #542), Recipes tab (under Library, #473) -- and
each is a bespoke hand-authored implementation of the same shape: a list of
installed things, each with a name, a status, and some actions. None goes
through `panel-spec.js`'s widget vocabulary (`list, detail, text, action,
group, cluster, toggle, table`). ADR-0031 already named this pattern
generally; this is the concrete third repeat (ADR-0028 R2) that earns a real
fix rather than a fourth bespoke tab when self_dev's diff history needs one
next.

Separately, Skills have no update path at all -- `plugins/skills.py` exposes
`install/uninstall/enable/disable/list/use`, nothing else. An installed
Skill is pinned forever at its install-time commit SHA.

## Decision

**1. A `registry` widget joins the panel vocabulary.** One row per installed
mechanism instance: name, status, a `VerifyResult` badge (`passed`/`evidence`
on hover/`score` if the mechanism produces one -- ADR-0034), and its actions
as `ps-action` buttons (declared tool calls, per ADR-0031's button
invariant). Plugins panel, the Skills sub-tab, and Recipes tab migrate onto
it incrementally, the same way ADR-0031 migrates hand-authored buttons --
not a rebuild, a swap of the row renderer.

**2. Nav stays where it is.** Plugins/Skills stay under Harness, Recipes
stays under Library. This is a shared *component*, not a nav consolidation --
#473 deliberately collapsed 16 routes to 4 and CONTEXT.md's Main window
entry already states new top-level sections are earned, not defaulted to.
Rejected a consolidated "Capabilities" section on this ground.

**3. `skill_update` ships, with a conflict rule borrowed from the
boot-rollback precedent.** ADR-0014's provenance (repo + commit SHA at
install) makes local-modification detection free: diff the installed copy
against that SHA's original content.
   - **Unmodified:** fetch latest, overwrite, bump the stored SHA.
   - **Locally modified:** refuse and surface "this Skill has local edits,
     update manually" -- never guess which version wins. Same principle as
     the Code load entry's *"the destructive reading must be the opt-in
     one"* and the boot rollback's *"never destructive"* stance on
     uncommitted work.

## Consequences

- Closes the Skills-has-no-update-button gap as a side effect of building
  the shared widget, not as a separate bespoke feature.
- A new mechanism's browsing UI is "add a row to the registry widget," not
  "build a tab" -- self_dev's diff history is the next obvious adopter.
- A silent-overwrite bug (updating over a hand-edit) is structurally
  prevented rather than relying on a warning dialog someone can click through.
