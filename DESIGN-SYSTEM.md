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

- **Active:** S12 -- #974
- **Model:** sonnet

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
- [x] S7 -- #969 -- Adaptive header density via container queries (Model: opus)
- [x] S8 -- #970 -- Shape scale: Harness + Library panes (Model: sonnet) -- hand-implemented, self_dev unreliable for this slice (see Lessons item 5)
- [x] S9 -- #971 -- Shape scale: Trading + Log panes (Model: sonnet) -- hand-implemented
- [x] S10 -- #972 -- Shape scale: Settings + Profiles panes (Model: sonnet) -- hand-implemented
- [x] S11 -- #973 -- Shape scale: Credentials + Permissions + Integrations panes (Model: sonnet) -- hand-implemented
- [ ] S12 -- #974 -- Shape scale: Memory + Insights + Recipes + Queue panes (Model: sonnet)
- [ ] S13 -- #975 -- Shape scale: remaining panes (Model: sonnet)

## Landed PRs

- PR #976 -- S1 (auto-merged by self_dev_campaign)
- PR #977 -- S2 (auto-merged by self_dev_campaign)
- PR #978 -- S3 (auto-merged by self_dev_campaign)
- PR #979 -- S4 (auto-merged by self_dev_campaign)
- PR #980 -- S5 (auto-merged by self_dev_campaign)
- PR #982 -- S6, run_id `design-system-s6-v2` (retried after PR #981 was closed for writing to an unlinked `tray/styles.css` + a bare `button` selector). #982 auto-merged with tests green but nested the new rule inside `.pane[data-route="harness"]`'s block -- hand-fixed in a follow-up commit, verified via jest + a live DOM check.
- PR #983 -- S7, run_id `design-system-s7-v2` (original spec asked for new click-to-reveal/click-to-open interactivity + linked an external artifact URL the sandbox can't fetch; two attempts under the original spec produced no commit at all. Scaled back to pure CSS visibility toggles, corrected on #969, then succeeded). #983 auto-merged with tests green but had two more bugs: it wrote to a new unlinked `tray/styles/header-container.css` (same dead-file mistake as #981) AND targeted a nonexistent `.hdr` class instead of the real `.header`, so it would have done nothing even if wired up. It also silently changed `.header`'s `flex-wrap` from `wrap` to `nowrap`, which broke an existing regression test guarding against a past incident (#325, header items bleeding past the content clip). Hand-fixed: moved the rules into `main.html`'s own `<style>` block under `.header`, restored `flex-wrap: wrap` as a last-resort fallback beneath the new container-query tiers, deleted the orphan file. Verified via jest (853/853) and a live resize check (header hides `.hdr-tts-vol`/inactive `.hdr-mic-seg` at narrow widths, shows everything at wide widths).
- (no PR) -- S8, hand-implemented directly. Two `self_dev` attempts (fresh run_ids `design-system-s8-v2`, `design-system-s8-v3`, the second after confirming the trading campaign had finished) both produced an identical, unrelated 1041-line diff touching `cerebral/trading/*`, `plugins/scheduler.py`, and `scripts/toggle-openclaw-gateway.ps1` -- tests correctly failed both times so nothing merged, but the failure was reproducible and not explained by staleness or concurrency (see Lessons item 5, and tracking issue #986). Implemented the actual sweep by hand instead: 15 `border-radius` declarations across `.hrns-*` (Harness pane) mapped onto the three tokens by role (secondary buttons/inputs -> sm, the toolbar search + card -> md, badge/tag pills -> lg); Library pane's own `.lib-*` rules have zero `border-radius` today, nothing to change there. Scrollbar-thumb radii (2px, three of them) and the one perfect-circle dot (50%) were deliberately left alone -- chrome conventions, not part of the shape-scale system. Verified via jest (859/859).
- (no PR) -- S9, hand-implemented (same pattern as S8, self_dev not retried given the confirmed pattern). Trading pane (`.trd-*`): 3 declarations mapped to md (create-toggle button, form inputs, submit button). Also found: `.trd-tabs`/`.trd-tab`/`.trd-panel`/`.trd-activity-section` (the actual tab bar and content panels) have **zero** CSS styling anywhere in the file -- they render as bare unstyled buttons/divs. Out of scope for a radius sweep (nothing to remap), but worth its own future issue if the Trading pane's tab bar should look like every other pane's. Log pane (`<div id="activity-log-mount">`, rendered by `tray/lib/activity-log.js`) has no dedicated CSS in `main.html` and zero `border-radius` usage in its own JS file -- nothing to change. Verified via jest (859/859).
- (no PR) -- S10, hand-implemented (same pattern). Settings (`.set-*`): 21 declarations mapped by role. Profiles (`.prof-*`, excluding `.prof-switcher-*`/`.prof-sw-*` which are header-scoped, already covered by S1/S2): 9 declarations. Left unchanged: scrollbar-thumb radii, the radio-button and toggle-thumb circles (50%), and the toggle-track's pill shape (kept as a literal px value tied to its own height, not bound to a scale token, same reasoning as the circles) -- all chrome conventions outside the shape-scale system. Verified via jest (859/859).
- (no PR) -- S11, hand-implemented (same pattern). Integrations (`.int-*`): 5 declarations. Credentials (`.cred-*`): 4. Permissions (`.perm-*`): 11, including a genuine badge (`.perm-plugin-row .perm-badge` -> lg). Left `.perm-fullauto-badge`'s `999px` unchanged (already a fully-rounded pill, not part of the discrete scale) and two status-dot circles (50%). Verified via jest (859/859).

## Lessons from S6, S7, and S8, for whoever picks up S9-S13

1. **This app has zero external stylesheets.** Every slice edits `main.html`'s
   own inline `<style>` block. Twice now (S6's first attempt, S7's only
   attempt) the model instead created a new `.css` file that nothing links,
   producing a merged PR with zero actual effect. If a slice's own PR touches
   any path under `tray/styles/` or `tray/lib/*.css`, that is a bug -- there
   is no such file in this app's real architecture.
2. **`self_dev_campaign` uses a deterministic run_id per slice** (issue #780's
   ledger). A slice whose run_id already has a recorded "pr" phase will
   resume by reporting that same recorded result rather than re-attempting
   the edit -- even after the PR is closed and the issue corrected. A genuine
   do-over needs a fresh run_id via the plain `self_dev` tool (not
   `self_dev_campaign`), and this file needs updating by hand afterward,
   since `self_dev` has no knowledge of this driver file.
3. **Passing jest is necessary, not sufficient.** Neither S6's mis-nested
   rule nor S7's wrong class name nor its silently-changed `flex-wrap`
   tripped a single test, because none of the tests parse CSS selector
   structure or diff computed styles against a baseline. Spot-check any
   slice's actual diff against the real file, especially when it touches an
   existing rule's boundaries rather than only adding new standalone rules.
4. **"Edit step produced no commit" is a different failure than a bad merge.**
   It means the model attempted nothing, most often because the ask exceeded
   what a mechanical CSS-sweep slice should require (new interactive JS, a
   reference the sandbox can't fetch since it has no network access during
   editing). If a slice fails this way, the fix is almost always to narrow
   the ask, not to just retry the same instructions again -- a same-run_id
   retry with unchanged instructions failed identically both times for S7.
5. **A slice can also produce a large, entirely unrelated diff, reproducibly.**
   S8 hit this twice with two different fresh run_ids: an identical
   1000+-line diff touching unrelated trading/backend code instead of the
   actual CSS task, both times with `tests_failed` (so nothing merged) and
   `guardrail_hit` on `cerebral/main.py`. Confirmed NOT caused by a stale
   clone (base commit verified as current master) or a concurrently-running
   campaign (the trading campaign was confirmed finished before the second
   attempt). Root cause unknown -- tracked in issue #986 for later
   investigation. If a slice's PR touches files nowhere near its own scope,
   don't try to salvage or merge any part of it -- close it and either retry
   once more with a fresh run_id or, if that also fails oddly, hand-implement
   the slice directly rather than continuing to retry against a possibly-
   broken edit path.

## SAFETY

Highest priority; overrides the issue if they ever conflict.

1. **Every slice is scoped to `tray/windows/main.html` only.** No slice in
   this queue touches `cerebral/`, `plugins/`, credentials, or the ADR-0005
   gate. If an issue's own scope ever seems to require touching something
   outside that file, stop and escalate rather than expanding scope. This
   includes never creating a new file under `tray/styles/` or anywhere else
   -- see "Lessons" item 1 above. If a run's own diff touches `cerebral/` or
   `plugins/` unprompted, treat it as broken (Lessons item 5) -- never merge
   or cherry-pick any part of it, regardless of what the CSS portion looks
   like.
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
   jest is necessary, not sufficient** -- see "Lessons" item 3 above. Spot-
   check a slice's actual diff when the change touches existing rule
   boundaries, not just new standalone rules.
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
