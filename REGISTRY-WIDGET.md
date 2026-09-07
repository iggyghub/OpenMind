# REGISTRY-WIDGET.md -- Registry widget + skill_update campaign driver

Consumed by Felix's own `self_dev` loop overnight (ADR-0015), not a
Claude-Code-run `run-*.ps1` loop. Each slice = one issue = one self_dev PR
(clone -> edit -> sandbox test gate -> PR), merged before the next
dependent slice starts. See docs/adr/0035-registry-widget-and-skill-update.md.

## Status: ready

## Next slice -- start here

- **Active:** G -- #1129
- **Model:** self_dev's `task_type="self_dev"` router pin (local/cloud/connected server, per ADR-0015).

## Queue

- [ ] G -- #1129 -- add `registry` to the panel vocabulary (foundation, blocks H-K)
- [ ] H -- #1130 -- migrate Plugins panel onto the registry widget
- [ ] I -- #1131 -- migrate Skills sub-tab onto the registry widget (blocks K)
- [ ] J -- #1132 -- migrate Recipes tab onto the registry widget
- [ ] K -- #1133 -- skill_update tool + local-edit conflict detection (depends on G, I)

H, I, J may run in parallel once G lands. K needs I's row to exist first.

## Landed PRs

## SAFETY

- This is Felix's own live UI (`tray/windows/main.html`, `tray/lib/panel-spec.js`)
  -- changes here go through the normal self_dev sandbox test gate and PR,
  never through `tools/ui-editor/`'s bake step (that bypasses the sandbox
  test gate entirely and is reserved for a human's manual, ad hoc edits --
  see ADR-0037's near-miss note and CONTEXT.md's Absorbed app entry).
- Nav placement does not change: Plugins/Skills stay under Harness,
  Recipes stays under Library. This campaign swaps the row *renderer*,
  never the sidebar structure (#473's four-section collapse stays intact).
- `skill_update` (K) must refuse on any local modification rather than
  guess which version wins -- never a silent overwrite.
