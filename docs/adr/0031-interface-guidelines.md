# ADR-0031: Interface guidelines -- unify the rules, not the rendering

**Date:** 2026-09-05
**Status:** Accepted (grill session)
**Extends:** ADR-0027 (visual token system), ADR-0012 (panel specs).
**Relates:** ADR-0007 (window architecture), ADR-0011 (absorbed apps),
ADR-0028 R7 (invocation ladder), ADR-0029 (adds a fifth window).

## Context

ADR-0027 introduced a visual token system. Measured a week later, its reach is
narrower than it reads.

**By surface** -- tokens exist in `main.html` only:

| Window | `--accent` | radius tokens | hardcoded hex |
|---|---|---|---|
| `main.html` | yes | yes | (the system) |
| `visualiser.html` | 0 | 0 | 24 |
| `irreversible-modal.html` | 0 | 0 | 16 |
| `detached-panel.html` | 2 | 0 | 9 |

**By property** -- inside `main.html`, ADR-0027 tokenised shape (3 radii),
elevation (2 shadows), semantic colour, the focus ring, and a 150ms transition.
It did not touch **typography (346 raw `font-size:`, no scale)** or **spacing
(269 `padding:` + 127 `gap:`, no scale)**. The campaign was triggered by ~140
inconsistent `border-radius` declarations; type and space are ~740 raw values,
five times the mass, untouched.

**And the toolkit serves guests, not the host.** `panel-spec.js` is a real widget
toolkit -- `list, detail, text, action, group, cluster, toggle, table` -- where
the renderer owns all drawing and a plugin cannot ship markup. More than that,
`action-widget.js` defines *behaviour*: a `.ps-action` carries `data-tool` and
`data-tool-args`, so **a button is a declared tool call** routed through the
ADR-0005 gate, not an `onclick`. That is an OS-grade invariant, already proven.
But `main.html` has **139 hand-written `<button>` against 9 `ps-action`**. The
toolkit draws plugin panels; Felix's own UI is hand-authored throughout --
backwards from an OS, whose own apps are the reference implementation of its
toolkit.

A third path exists and cannot be styled at all: an **absorbed** external app
(LibreOffice, ADR-0011) draws its own chrome, as do the native tray menu and OS
notifications.

## Decision

**Unify the rules, not the rendering.** Rebuilding `main.html`'s 13,000 lines onto
an eight-widget vocabulary was the alternative -- the textbook OS answer, and a
rewrite with no user-visible payoff. Rejected.

1. **One token file, shared by every Felix-drawn window.** All five, including
   ADR-0029's new `boot.html`. No window starts its own palette.

2. **Type and space join the token set.** They are the largest source of
   inconsistency and everything above them in the visual stack -- named patterns,
   consistent surfaces -- is downstream of them. A "pane header" pattern is
   meaningless while every header picks its font-size from nowhere.

3. **The button invariant is promoted from a panel-spec implementation detail to
   a Felix-wide rule: a button declares its tool.** Hand-authored surfaces migrate
   onto the contract incrementally, button by button, without rebuilding the panes
   around them. This is the behavioural half of the design system and the more
   load-bearing one.

4. **Scope is Felix's own pixels.** Native tray menus and OS notifications
   deliberately follow the host -- for a product whose premise (ADR-0016) is
   operating the user's existing apps rather than replacing them, host chrome
   *should* look like the host. Custom-drawing them would also forfeit the
   accessibility and focus behaviour the native ones provide free.

5. **Accessibility is in scope; theme is deferred.** These are the two
   cross-cutting layers, and the two that get roughly ten times more expensive to
   retrofit -- today there are **0** `prefers-color-scheme` rules and **36** ARIA
   attributes across 13,000 lines. They are split because they are not the same
   kind of thing. Light mode is *preference*, and the user is currently the only
   user, so it can wait until there is someone it serves. Keyboard reach, focus
   order, and labelling are what make the interface usable **at all** when voice
   is not an option -- an outage, a muted mic, a shared room -- so they are part of
   the guidelines rather than a later campaign. ADR-0027's global focus ring is the
   start of that work, not the end of it.

6. **An absorbed app is made consistent behaviourally, never visually.** Felix
   will not restyle LibreOffice. What it controls is how the app is invoked and
   where its output lands -- the Document library already does this.

## Consequences

- The five windows stop drifting. Today four of them are outside the system
  entirely.
- Migration is incremental at every point: tokens can be extracted per-window,
  type/space per-pane, buttons one at a time. Nothing requires a big-bang change.
- 139 hand-written buttons become visible technical debt with a defined
  destination, rather than an unremarkable status quo.
- The design system stops meaning "colours". Its centrepiece is a behavioural
  invariant that happens to already work.

## Open

Nothing. Both cross-cutting forks were resolved into decision 5 (2026-09-05);
the deferral of theme is a decision with a stated trigger -- a second user -- not
an unanswered question.
