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
