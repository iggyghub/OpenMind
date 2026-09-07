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
