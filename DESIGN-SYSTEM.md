# DESIGN-SYSTEM.md -- UI visual token system campaign driver

Autonomous slice loop via Felix's `self_dev_campaign` (ADR-0015). Design:
`docs/adr/0027-ui-visual-token-system.md`. Grilled/scoped 2026-08-30 from a
live design pass on the Conversation header (already shipped directly, not
part of this queue) plus a full-file audit of shape/elevation/color/focus
consistency.

Each slice reads this file + its issue, implements that ONE slice exactly as
the issue specifies, opens a per-issue PR (`Closes #N`), and this file's
"Next slice" block gets rewritten. This file is the only memory between runs.

## Status: ready

## Next slice -- start here

- **Active:** S7 -- #969
- **Model:** opus

## Queue

S1 is the foundation every other slice depends on -- do not reorder S2-S7
ahead of it. S8-S13 (the shape-scale sweep) all depend on S1 only, not on
each other or on S2-S7, and may run in any order or in parallel once S1 is
merged.

- [x] S1 -- #963 -- UI tokens: shape/elevation/semantic-color custom properties (Model: sonnet)
- [x] S2 -- #964 -- Floating-shadow sweep: standardize popovers on --shadow-floating (Model: sonnet)
- [x] S3 -- #965 -- Transition-duration sweep: standardize to 150ms (Model: sonnet)
- [x] S4 -- #966 -- Icon sweep: replace remaining glyph icons (Model: sonnet)
- [x] S5 -- #967 -- Semantic color sweep: health/state colors onto named tokens (Model: sonnet)
- [x] S6 -- #968 -- Global focus-visible ring (Model: opus)
- [ ] S7 -- #969 -- Adaptive header density via container queries (Model: opus)
- [ ] S8 -- #970 -- Shape scale: Harness + Library panes (Model: sonnet)
- [ ] S9 -- #971 -- Shape scale: Trading + Log panes (Model: sonnet)
- [ ] S10 -- #972 -- Shape scale: Settings + Profiles panes (Model: sonnet)
- [ ] S11 -- #973 -- Shape scale: Credentials + Permissions + Integrations panes (Model: sonnet)
- [ ] S12 -- #974 -- Shape scale: Memory + Insights + Recipes + Queue panes (Model: sonnet)
- [ ] S13 -- #975 -- Shape scale: remaining panes (Model: sonnet)

Per-slice model: sonnet unless the queue entry says otherwise. S6 and S7 are
marked opus because they involve more judgment (finding every unguarded
interactive control; getting three responsive tiers right) than the
mostly-mechanical find/replace slices around them. When ticking a slice, set
the next entry's model on the `Model:` line above if it differs from sonnet.

## Landed PRs

- PR #976 -- S1 (auto-merged by self_dev_campaign)
- PR #977 -- S2 (auto-merged by self_dev_campaign)
- PR #978 -- S3 (auto-merged by self_dev_campaign)
- PR #979 -- S4 (auto-merged by self_dev_campaign)
- PR #980 -- S5 (auto-merged by self_dev_campaign)
- PR #982 -- S6, run_id `design-system-s6-v2` (retried after PR #981 was closed for two mistakes: wrote to a new `tray/styles.css` main.html never links, and used a bare `button` selector). #982 itself auto-merged with tests green but landed a corrupted CSS rule (its insertion split `.pane[data-route="harness"]`'s block in two, nesting the new selectors inside it -- passing jest tests don't cover CSS parse structure). Hand-fixed directly in a follow-up commit; verified via a fresh `npx jest` run (853/853 pass) and a live DOM check confirming `.nav-item:focus-visible` etc. is a proper top-level rule and the harness pane's own rule is intact.

## Landed via self_dev_campaign's own deterministic-run_id ledger (issue #780): a
slice whose run_id already has a recorded "pr" phase will resume by reporting
that same recorded result rather than re-attempting the edit, even after the
PR is closed and the issue is corrected. If a slice needs a genuine do-over
after its first PR is closed, call the plain `self_dev` tool directly with an
explicit fresh `run_id` (e.g. `design-system-s6-v2`) and the corrected
change_description, rather than re-invoking `self_dev_campaign` and expecting
it to retry -- it won't. Update this file by hand afterward; `self_dev`
(unlike `self_dev_campaign`) has no knowledge of this driver file.

## SAFETY

Highest priority; overrides the issue if they ever conflict.

1. **Every slice is scoped to `tray/windows/main.html` only.** No slice in
   this queue touches `cerebral/`, `plugins/`, credentials, or the ADR-0005
   gate. If an issue's own scope ever seems to require touching something
   outside that file, stop and escalate rather than expanding scope.
2. **Never remove or rename an existing `id` or class a script references.**
   Grep the file's own `<script>` block for `getElementById`/
   `querySelector`/`classList` before touching a selector that has JS
   behavior attached. Visual-only changes (radius, shadow, color, adding an
   SVG inside an existing wrapper) should never need an id/class rename; if a
   slice seems to require one, that's a sign of scope creep -- stop and
   escalate.
3. **No plugin-authored HTML or JS may reach a renderer** (ADR-0012 decision
   3, unchanged by this campaign). Nothing in this queue touches panel specs
   or plugin-contributed content.
4. **Gate on tests:** `npx jest` in `tray/` must pass before opening the PR.
   This campaign has no backend/Python surface, so `pytest` is not expected
   to be affected -- if a slice somehow touches `cerebral/`, that is out of
   scope (see rule 1) and the run should stop rather than proceed. **Passing
   jest is necessary, not sufficient** -- S6 proved a CSS edit can land with
   a corrupted rule structure (a new rule nested inside an unrelated existing
   one) while every jest test still passes, since none of them parse CSS
   structure. Spot-check a slice's actual diff when the change touches
   existing rule boundaries, not just new standalone rules.
5. **No new dependency.** Every slice in this queue is achievable in plain
   CSS/inline SVG/vanilla JS already used elsewhere in the file. Adding an
   icon library, a CSS framework, or any `tray/package.json` entry is out of
   scope for all thirteen issues.
6. **Behaviour only verifiable by eye in the live Electron window** (does the
   header actually adapt at each width, does a focus ring actually appear on
   tab) -> append an item to `docs/harness-ui-live-verify.md` rather than
   skip it. Logic that can be asserted in jest (e.g. "this selector's
   computed style references `var(--radius-md)`") should still get a test.
7. **This campaign auto-merges** (ADR-0015's full-auto-merge posture) and
   each merge restarts Cerebral + Electron. If you launch Cerebral yourself
   to smoke-test IPC mid-slice, launch it in the background and always
   terminate it before finishing -- leave no orphan `python -m cerebral.main`.
8. **A shape-scale slice (S8-S13) must not touch another cluster's pane.**
   Each of those six issues names its exact pane scope; grep for the
   `data-route` values it lists and stay inside them. Overlapping edits
   between two shape-scale PRs running close together is the failure mode
   ADR-0027's "Rollout" section specifically split slices to avoid.
