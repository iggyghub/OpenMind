# ADR-0037: The absorbed-app contract, generalized from ADR-0011

**Date:** 2026-09-06
**Status:** Accepted (grill session)
**Relates:** ADR-0011 (LibreOffice as editor+engine+converter -- the
originating case), ADR-0031 (interface guidelines -- "scope is Felix's own
pixels," the boundary this generalizes), the **Document library** entry in
CONTEXT.md, `UI-EDITOR.md` (the case that looked like a counter-example and
was not).

## Context

OpenMind's roadmap already commits to absorbing more OSS GUI tools
(CONTEXT.md's "Second wave": GIMP/Darktable, Blender, Figma). Each one
brings its own native UI Felix cannot reskin -- ADR-0031 already drew that
boundary for LibreOffice specifically ("an absorbed app can only be made
consistent behaviourally"). Rather than re-deriving ADR-0011's reasoning
from scratch at the next absorption, this generalizes the reusable parts
into a standing contract, written ahead of the usual third-repeat bar (R2)
because it costs nothing -- a documented rule, not new code -- and the next
1-2 repeats are already scheduled in the roadmap.

**A near-miss worth recording.** `tools/ui-editor/` (`UI-EDITOR.md`) looked
like a counter-example -- a from-scratch website-builder tool "self-grilled
against GrapesJS/Webflow/Wix-style builders" without absorbing any of them.
It is not a counter-example: its actual job is injecting a click-to-edit
overlay onto *arbitrary existing HTML, including Felix's own live
`tray/windows/*.html`*, and baking overrides back into the source file --
a page-*editor*-of-anything-already-there, not a page-builder-from-a-canvas.
No absorbed tool covers that shape, and the renderer's own no-bundler/
no-framework rule (already applied to reject CodeMirror for the **Text
widget**) would have ruled out embedding GrapesJS regardless. The contract
below explicitly does not apply to it.

## Decision

**The absorbed-app contract applies when Felix launches an external GUI
program so the user (or Felix, headlessly) can do a creative/document task
on the program's own turf** -- LibreOffice today; GIMP/Darktable, Blender,
Figma next. It does **not** apply to tools that edit Felix's own live
surfaces (`ui-editor`) or to plugins with no GUI at all (Home Assistant,
Reddit, Twitter/X -- these are API integrations, not absorbed apps).

For anything in scope, four properties, generalized from ADR-0011:

1. **Invocation.** Opened as its own program via the R7 invocation ladder
   ("Felix, open my [X]"). Never rebuilt inside Felix's own chrome.
2. **Output landing.** Files land in the Document library (or the
   equivalent store for the app's file type) by default -- never loose on
   disk, mirroring the existing Document library rule.
3. **Turn-taking, not co-editing.** Felix drives headlessly where the app
   exposes a scripting surface (UNO-style), or the user drives natively --
   never simultaneously. No reconciliation layer; last-write-wins on the
   file, as ADR-0011 already accepted for `.docx`.
4. **No Felix-built reskin, ever.** Reaffirms ADR-0031's boundary,
   generalized from "LibreOffice" to every future absorption in scope here.

## Consequences

- The next absorption (GIMP/Darktable is the most likely first) follows
  this contract instead of a fresh grill re-deriving ADR-0011's reasoning.
- `ui-editor` and any future Felix-self-editing tool stays explicitly
  outside this contract -- it is governed by the renderer's existing
  no-framework rule, not this one.
- A future absorption candidate that doesn't cleanly fit either bucket
  (creative GUI app vs. headless API integration) is the signal to revisit
  this ADR, not a reason to stretch the contract to cover it.

## Amendment (2026-10-13) -- a second path: the reconstructed plugin (supersedes decision 4's "ever")

**Context** -- Decision 4 ("No Felix-built reskin, ever") was written when
the only way to absorb a GUI program was to launch it and drive it from
outside. REA (`morluto/rea`, MCP + CLI reverse-engineering toolkit) changes
what is reachable: Felix can inspect an external app -- binary, Electron
bundle, website, .NET assembly -- explain how a feature works with evidence,
and *rebuild it*. The user's goal for the REA work is exactly that: have the
GUI as a page of Felix's own, then change it around. Decision 4 as written
forbids the headline use case, so it is wrong for that path.

**Decision** -- Decision 4 is **scoped, not deleted**. Two paths now exist:

1. **Launched app (unchanged).** LibreOffice and any program Felix opens as
   its own window. Decisions 1-4 stand verbatim: the external window is never
   re-skinned, Felix and the user take turns, output lands in the Document
   library.
2. **Reconstructed plugin (new).** Felix uses REA (or equivalent evidence-bearing
   analysis) to understand an external app's feature, then builds a
   **Felix-native sidebar page** that reimplements it. This is not a reskin of
   the external program -- it is Felix-owned code, in Felix's own pixels, and
   it replaces the need to launch the original for that feature.

Properties of the reconstructed path:

- **It is a plugin-contributed panel** (ADR-0012) plus a plugin for any
  behaviour, built through `self_dev` (ADR-0015). It is Felix source, so the
  user can change it afterwards with `self_dev` or the ui-editor -- that
  editability is the point.
- **ADR-0031 applies in full.** It is Felix's own pixels: shared visual
  tokens, and every button is a declared tool call through the ADR-0005 gate.
- **Provenance is recorded.** The page's plugin keeps the REA version, target,
  and the evidence/limitations the reconstruction was built from, so the
  claim "this reimplements X" is inspectable.
- **Analysis is gated like any other tool.** Static analysis rides the
  existing capability classes; runtime capture of a target runs it with the
  user's permissions and must be declared and consented per ADR-0005.
- **Reconstruction is behavioural, not byte-for-byte.** Felix reimplements
  the observed behaviour; it does not copy or redistribute the target's
  assets or code.

**Terminology.** Both paths are **GUI plugins**: a plugin whose surface is
a GUI, either an external program Felix launches (path 1) or a Felix-built
page (path 2). How they are surfaced to the user (the hub, pinning, sidebar
ordering) is ADR-0039.

**Considered and rejected**

- *Delete decision 4 outright.* Would silently license re-skinning launched
  apps like LibreOffice (ADR-0011), which still cannot be re-skinned and
  still depend on the turn-taking rule.
- *Leave ADR-0037 as is and treat REA pages as an exception.* The exception
  is the intended main path; an ADR that only describes the exception rots.
- *Patch or modify the external app's binary in place.* REA is analysis,
  not instrumentation-for-modification; in-place patching adds update-breakage risk with no benefit over owning the reimplementation.
- *Absorbed-app contract for headless tools only.* Rejected earlier in this
  ADR as the "plugin, not absorption" bucket; REA itself stays a plugin
  (headless CLI/MCP). What is "absorbed" here is the *target's feature*.

**Consequences**

- REA is a **plugin** (`plugins/rea.py` + its panel); the things it
  analyses may become **reconstructed plugins**. A new CONTEXT.md term records
  this.
- ADR-0031 decision 6 ("an absorbed app is made consistent behaviourally,
  never visually") remains true for *launched* apps only; a reconstructed plugin is Felix's own surface and follows the full guidelines.
- The 16-class capability vocabulary (ADR-0005) is **unchanged**; no new
  class is introduced.
