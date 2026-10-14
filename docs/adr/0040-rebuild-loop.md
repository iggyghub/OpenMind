# ADR-0040: The rebuild loop -- evidence, spec, build, score, iterate

**Date:** 2026-10-14
**Status:** Accepted (user direction: "the end goal is to rebuild")
**Relates:** ADR-0037 (the 2026-10-13 amendment: reconstructed plugins),
ADR-0039 (Hub), ADR-0015 (self_dev), ADR-0023 (the self-modifying loop stays
external), ADR-0012 (panel vocabulary), ADR-0028 (R1 reach ladder, R6 verified
running), ADR-0005 (gate).

## Context

REA (`plugins/rea.py`) can analyse an installed Electron app and return
evidence. Nothing yet turns that evidence into a built feature, or measures how
close the build is. Without a measure, "Felix rebuilt it" is a claim, not a
result. A test on Felix's own `tray/` (2026-10-14) showed what REA's evidence is
good for and where it is blind: windows, main-process IPC handlers and exposed
bridge APIs matched ground truth; preload wiring through computed paths and
IPC calls made from inline scripts in HTML were missed.

## Decision

1. **A rebuild is a directory**, `cerebral/data/rebuilds/<name>/` (gitignored):
   `spec.json`, `manifest.json`, `scorecard.json`, and references to REA runs.
   The built code itself goes through self_dev as ordinary PRs.
2. **The spec is derived mechanically from REA's compact application graph**
   (`normalized_result.graph`: windows, ipc-channel/ipc-handler pairs,
   context-bridge APIs, endpoints, storage, with `handles`/`invokes`/`exposes`/
   `persists_to` edges), not from the multi-megabyte semantic graph. Every
   fact gets a stable id (`F-<kind>-<n>`), its REA node id, REA's confidence,
   and the run's limitations; REA's unknowns are carried as `unknowns`, never
   dropped. A model may add an `intent` sentence per feature but may not add
   facts.
3. **A spec is for one named feature**, a subset of facts chosen by the user
   or by Felix. Whole-application rebuilds are out of scope for v1.
4. **Build = a self_dev campaign.** Panel vocabulary first (ADR-0012); new
   widget types are their own reviewed slices. Built code declares which spec
   facts it implements in `manifest.json` (`fact id -> symbol, test`).
5. **Score is deterministic, never an LLM judge.** `rebuild_score` marks each
   fact `met` (manifest entry exists, the referenced test exists and passes),
   `unmet`, or `unknown` (REA itself was unsure). The scorecard states plainly
   that v1 measures **static coverage of the evidence**, not behavioural
   equivalence. Behavioural comparison needs runtime observation (REA's
   Electron/browser observation) and is a later decision.
6. **Iterate, bounded and operator-driven.** Unmet facts are turned into a
   `self_dev_campaign` driver (one slice per group). At most 3 rounds, then
   stop and report. Felix may start a round; an unbounded self-modifying loop
   stays external per ADR-0023. Every round is an ordinary PR through the gate.
7. **Evidence is untrusted input.** Strings from a third-party app flow into
   build prompts, which is a prompt-injection surface. Spec fields are
   structured and length-capped, app text is quoted as data and never placed in
   instructions, builds are confined to the self_dev safe zones, and nothing
   copies the target's assets or code (behaviour only, per ADR-0037).
8. **Order of targets:** Felix's own `tray/` first, because the answer key is
   the source and it validates the extractor and scorer; then one feature of
   an open-source app (Signal Desktop) found by name.

## Considered and rejected

- **LLM-judged "how close is it".** Unfalsifiable and drifts with the model.
- **Whole-app rebuild as the unit.** No feature boundary, nothing testable.
- **Spec from the full semantic graph.** 30+ MB, mostly expression nodes; the
  compact graph already holds the structural facts.
- **Felix running the loop unattended.** Violates ADR-0023 decision 1.

## Consequences

- New plugin `plugins/rebuild.py` (tools: `rebuild_new`, `rebuild_score`,
  `rebuild_driver`) and, later, a section in the REA panel. Slices are
  RB1 spec extraction, RB2 scorer, RB3 panel, RB4 driver generation.
- The 16-class capability vocabulary (ADR-0005) is unchanged: the plugin reads
  and writes files only.
- Each slice updates the REA Help topic in the same PR.
- v1 cannot say "this behaves the same"; it can say "these N of M observed
  facts are implemented and tested". That is the honest ceiling until runtime
  observation is wired.

## Amendment (2026-10-14) -- first closed loop, and what it changed

**Context** -- The first real run of the loop (`felix-dialogs`, a 5-fact scope of
Felix's own `tray/`: the `felixDialogs` bridge and its two IPC channels and
handlers) was built by Budd through self_dev from the spec alone, with no
code supplied, and scored with `rebuild_score`.

**Result** -- Round 1: 0 of 5 met (tests failed at import: it invented
`cerebral.tools`). Round 2, given the failure and the plugin conventions:
5 of 5 met, but the PR failed the repo's AST completeness check. Round 3:
a one-line hand fix (declare `fs_write`, as `plugins/rea.py` does) and merge.
Final live score: 5 of 5 met (100%).

**Decisions**
1. **Manifest location.** A build's manifest lives in the repo at
   `plugins/rebuild_manifests/<name>.json` so it travels with the build PR;
   the spec and scorecard stay under `cerebral/data/rebuilds/<name>/`
   (gitignored). This was implied by decision 5 and is now explicit.
2. **A build issue must state the plugin conventions and the capability
   rule**, because the builder model does not know them: the
   `cerebral.mcp.orchestrator` import, the `Tool`/`ToolResult` shape,
   module-level `PLUGIN_NAME`/`REQUIRED_CAPABILITIES`/`create()`, and that any
   plugin that launches a process through `run_fn` must declare `fs_write` or
   `shell_exec` (`test_call_site_capabilities`). Failures were convention
   failures, not design failures.
3. **Specs may be scoped** (`rebuild_new ... scope=[...]`) with fact ids stable
   across scopes, so a manifest survives a re-scoped spec.
4. **The scorer's "met" requires a passing test, not only a mapping**, which is
   what made round 1 score 0 rather than a misleading 100%.

**Considered and rejected** -- Letting the scorer count a manifest entry as met
without running its test: it would have reported full coverage for round 1,
whose code could not even be imported.

**Consequences** -- The 16-class capability vocabulary is unchanged. Remaining:
RB3 (panel view) and RB4 (campaign generation from unmet facts); until RB4
exists the round-to-round feedback is written by hand.

