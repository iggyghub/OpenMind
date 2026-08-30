# 27. A visual token system for the Main window: shape, elevation, semantic color, focus, density

Date: 2026-08-30
Status: accepted

## Context

The Main window (`tray/windows/main.html`, ~13,000 lines) grew across roughly
twenty separate UI campaigns (ADR-0007, the two UI-overhaul rounds, harness
parity, skills, self-dev, trading, ...). Each one styled its own corner of the
app in isolation. A design pass on the Conversation header and sidebar this
session (icons, the two header "wells", hover/focus states) surfaced the
underlying pattern by auditing the whole file:

- **Icons**: nav items and a few controls used raw Unicode glyphs (`✦ ⚙ ☰ ⌖ ≡
  ◆`, `📎`, `⚠`, `✶`) standing in for icons — inconsistent weight, no shared
  grid.
- **Shape**: ~140 `border-radius` declarations span nearly every integer from
  2px to 12px (plus a couple of pill radii), no shared scale.
- **Elevation**: four different floating surfaces (the health panel, the
  profile-switcher dropdown, federated search results, one modal) each use a
  *different* box-shadow for the same job — `8/24/.4`, `4/16/.4`, `8/30/.4`.
- **Semantic color**: "Speaking" state and the queue-count badge both just
  reuse `var(--accent)` — the same variable that also drives brand identity —
  so changing the accent later would silently change what those two things
  *mean*, not just how they look.
- **Focus**: tabbing through the app shows a visible keyboard-focus ring on
  exactly three controls (two toggle switches, one consent button); everywhere
  else shows nothing. Where a focus color does exist, it's one of five
  different one-off hex values, not `var(--accent)`.
- **Density**: the header already wraps to a second row at narrow widths
  (`flex-wrap: wrap`, F2 #325) rather than adapting — ugly, but functional.

None of this is a `main.html` rewrite. It's the accumulated cost of never
having a shared vocabulary for "how rounded," "how raised," "what color means
what," and "how does keyboard focus look" — the same class of gap ADR-0012
already closed for panel layout and ADR-0007 for window architecture, just one
level closer to the pixels.

## Decision

Introduce five small, named token systems as CSS custom properties in
`main.html`'s existing `:root` block (which already holds `--accent`,
`--bg`, `--border`, etc. — this extends that block, it does not replace it).
None of these are new mechanisms; each is the value that already won an
informal plurality in the file, made explicit and reusable.

1. **Shape scale — "Soft."** `--radius-sm: 6px; --radius-md: 10px;
   --radius-lg: 14px;`. Small = inline chips/segments, medium = buttons and
   input fields, large = pill-shaped badges and dropdowns. Already the shape
   used by the two header wells shipped this session.

2. **Elevation — two tiers, not the four ad hoc ones found in the audit.**
   `--shadow-raised: 0 1px 3px rgba(0,0,0,.35);` (a control sitting on the
   page — "this is one thing"), `--shadow-floating: 0 8px 24px
   rgba(0,0,0,.4);` (a surface above the page — dropdowns, popovers, modals).
   Every floating surface in the file converges on the *existing* most-common
   value (`8/24/.4`) rather than inventing a new one. A component is flat at
   rest by default; `--shadow-raised` applies on hover, `--shadow-floating`
   applies on focus (see 4) — this is a state ladder, not a per-component
   choice.

3. **Semantic color — split by intent, not merged into accent.**
   `--color-success`, `--color-warning`, `--color-danger` (the existing
   green/amber/red already used for connection health, renamed and
   centralized). "Speaking" and the queue badge **keep** using `var(--accent)`
   — on purpose. They mean "Felix is doing something," not "something is
   wrong," and a future accent change (see ADR's "Consequences") is allowed to
   recolor them; a future accent change must **not** be able to recolor
   connection health, which is why that gets its own fixed tokens instead of
   also riding on accent.

4. **Focus = float, always.** Any focusable control jumps to
   `--shadow-floating` plus a soft accent glow
   (`0 0 10px rgba(124,92,252,.25)`) on `:focus-visible`, **regardless of its
   resting tier** — a flat nav item and an already-raised header well both
   land on the identical floating-plus-glow treatment when focused. Hover
   (mouse) only ever reaches `--shadow-raised`, no glow — shadow means
   "hoverable," glow means "this one is active right now." This closes a real
   accessibility gap (see Context), not just a style inconsistency; it is not
   optional the way 1-3 are stylistic.

5. **Density is adaptive, not a fixed choice.** The header uses CSS container
   queries (`container-type: inline-size` on the header itself, not the
   window) with three tiers — Comfortable (≥640px available), Compact
   (460-640px: the TTS slider folds behind its icon, the profile chip drops
   its name to an initial), Icon rail (<460px: mic-mode folds to one icon,
   the queue badge drops to a bare count). The state pill never compresses at
   any tier — it is the single highest-value glance in the header. Because
   the query targets the header's own box, it also adapts correctly when the
   sidebar collapses or a secondary workspace panel (ADR-0012) opens beside
   Conversation, without extra wiring for either case.

Icons that are still raw glyphs standing in for a concept (the `✶`/`⚒` empty
states in Memory/Insights/Recipes, the two `⚠` warnings) get the same
stroke-SVG treatment the nav icons already received. Chevrons/carets (`▾ ◀ ▶
› ⋮`) are explicitly **not** in scope — those are conventional wayfinding
glyphs, not icons standing in for a distinct concept, and the audit found no
real problem with them.

## Rollout

This is implemented as a Felix `self_dev_campaign` (ADR-0015), not a single
PR — the tokens themselves are a small, safe foundation slice; the *sweep*
touches every pane and is deliberately split into small, single-concern
slices so a bad edit in one has a small blast radius and doesn't block the
rest. Tracked in `DESIGN-SYSTEM.md` (same driver-file format ADR-0015's
campaign amendment already established) against issues labeled
`ready-for-agent`. `tray/` is a classified guardrail path (ADR-0015), but
guardrail classification is informational only under the current full-auto-
merge posture — these slices auto-merge and restart Cerebral + Electron like
any other self-dev change; nothing here asks for that to be tightened.

Order: token definitions first (zero visual change — the header wells and
existing hardcoded values already equal these tokens' values), then the
small mechanical sweeps (floating-shadow, transition-duration, remaining
icons, semantic color, global focus ring, header density), then the shape-
scale sweep pane-by-pane last, since it is the largest single change
(~140 call sites) and benefits most from everything else already being
stable underneath it.

## Considered options

- **One giant PR sweeping the whole file at once**: rejected — a 13,000-line
  file with no automated visual-regression coverage means a single large diff
  has no way to catch a bad radius/shadow substitution except a full manual
  pass; small slices bound the damage of any one mistake to one pane.
- **A fourth+ elevation tier** (e.g. separate values for modals vs.
  dropdowns): rejected — the audit found real popovers already converging
  informally on one value; inventing more tiers than the app actually
  distinguishes would recreate the inconsistency problem this ADR exists to
  remove.
- **Accent-tinted health/connection colors** (so the whole app recolors
  together): rejected — reject by design goal, not oversight. Red-for-down /
  green-for-ok is a universal convention; letting an accent swap silently
  desaturate or hue-shift "the server is down" would actively hurt
  scannability for a taste preference.
- **A hue-slider custom accent picker**: raised and explicitly parked, not
  decided here — out of scope for this ADR; revisit separately if wanted.

## Consequences

- `--accent` / `--accent-dim` remain the only two variables a future accent
  change needs to touch; nothing added here creates a second place accent
  logic lives.
- The shape-scale sweep is genuinely large (~140 call sites across ~15
  panes) — expect it to be the majority of this campaign's slices and its
  longest-running track.
- The focus-ring slice closes a real accessibility gap (visible keyboard
  focus on nearly every control) as a side effect of a visual-consistency
  pass, not a dedicated accessibility audit — worth remembering this is a
  floor, not a full accessibility review.
- No new dependency, no build step, no plugin-facing surface changes — this
  is confined to `tray/windows/main.html`'s existing `<style>` block and
  markup.
