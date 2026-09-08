# REGISTRY-WIDGET.md -- Registry widget + skill_update campaign driver

Consumed by Felix's own `self_dev` loop overnight (ADR-0015), not a
Claude-Code-run `run-*.ps1` loop. Each slice = one issue = one self_dev PR
(clone -> edit -> sandbox test gate -> PR), merged before the next
dependent slice starts. See docs/adr/0035-registry-widget-and-skill-update.md.

## Status: ready

## Next slice -- start here

- **Active:** K -- #1133
- **Model:** self_dev's `task_type="self_dev"` router pin (local/cloud/connected server, per ADR-0015).

## Queue

- [x] G -- #1129 -- add `registry` to the panel vocabulary. PR #1151's widget code was correct but its own test used a require path one level too deep, resolving outside the repo; hand-fixed (commit 7d732fb), also updated the pre-existing WIDGET_TYPES exact-match test. PR #1151 closed unmerged.
- [x] H -- #1130 -- migrate Plugins panel onto the registry widget. Blocked mid-campaign on a real design gap (enable/disable was a tray-internal WS event, not a declared MCP tool -- see ADR-0031). Resolved by hand: added `plugin_set_enabled` tool to `plugins/settings_control.py` (commit fcd42a6), then swapped the drawer's toggle button to dispatch it via `call_tool` (commit 94457a8), keeping the card-grid/drawer structure itself untouched. A self_dev attempt (PR #1152, closed) built a disconnected fake widget with hardcoded data and corrupted a `<style>` block -- not reattempted.
- [x] I -- #1131 -- migrate Skills sub-tab onto the registry widget. Hand-implemented directly, commit 4495dec. `plugins/skills.py`'s `panel_spec()` now emits one registry row per skill with a real VerifyResult badge; extended `_renderRegistry` with an optional `hint` field.
- [x] J -- #1132 -- migrate Recipes tab onto the registry widget. Same design gap as H (run/delete were WS events) -- resolved by adding `recipe_run`/`recipe_delete` tools to a new `plugins/recipes.py` (commit 1939ca6), then migrating `renderRecipes()` onto `PanelSpec.renderWidget({type:'registry',...})` + `ActionWidget.initActionWidgets` (commit 94457a8). Also added a per-recipe VerifyResult (dry-run replay, ADR-0034) to the `recipes_update` broadcast for the badge, and added the `.ps-registry-*`/`.ps-verify-*` CSS that slices G/I had shipped without (Skills gets it for free too).
- [ ] K -- #1133 -- skill_update tool + local-edit conflict detection (depends on G, I -- both done)

## Landed PRs

- G -- hand-fixed on master (7d732fb); PR #1151 closed unmerged, superseded
- H, I, J -- all hand-implemented on master (no clean self_dev PR for any of the three); see Queue notes above for commits

## SAFETY

- This is Felix's own live UI (`tray/windows/main.html`, `tray/lib/panel-spec.js`)
  -- changes here go through the normal self_dev sandbox test gate and PR,
  never through `tools/ui-editor/`'s bake step (that bypasses the sandbox
  test gate entirely and is reserved for a human's manual, ad hoc edits --
  see ADR-0037's near-miss note and CONTEXT.md's Absorbed app entry).
- The blast-radius gate's guardrail block is informational-only as of the
  2026-08-21 amendment -- test status is the only real merge gate now. A
  tray/panel-spec change is not a guardrail path anyway, so this doesn't
  change this campaign's risk much, but don't assume any human sees a
  diff before it merges.
- Nav placement does not change: Plugins/Skills stay under Harness,
  Recipes stays under Library. This campaign swaps the row *renderer*,
  never the sidebar structure (#473's four-section collapse stays intact).
- `skill_update` (K) must refuse on any local modification rather than
  guess which version wins -- never a silent overwrite.
