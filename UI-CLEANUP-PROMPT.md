# UI Cleanup -- guided session prompt

Paste everything below the line into a fresh Claude Code session in `C:\OpenMind`.

---

I want to clean up Felix's UI and I need you to **guide me through it**. I'm not a
designer -- you lead, I react. Ask me one thing at a time, show me what you mean
(screenshots, before/after), and recommend a pick at every fork instead of handing
me a menu.

## Ground rules

1. **Read before proposing.** Before suggesting anything, read:
   - `docs/adr/0007-main-window-ui-architecture.md` (window layout)
   - `docs/adr/0012-workspace-and-plugin-contributed-panels.md` (workspace, panel specs)
   - `docs/adr/0027-ui-visual-token-system.md` (shape, elevation, color, focus tokens)
   - `docs/adr/0031-interface-guidelines.md` (type/spacing scale, toolkit-first)
   - `BASE-DESIGN-SYSTEM.md` and `DESIGN-SYSTEM.md` (baseline rules + scanner)
   - CLAUDE.md rule R7 (invocation ladder: nothing earns a menu item/route/hotkey on first ask)
2. **Reconcile against what already shipped.** Past UI prompts were stale on most of
   their threads. For every problem I raise, check git log / ADRs / memory first and
   tell me if it's already built, already decided, or genuinely open.
3. **Look at the real app, not just the code.** `tray/windows/main.html` is ~14k
   lines; reading it won't show you what I see. Launch the tray (or use the built-in
   browser / screenshots) and look at each screen before judging it.
4. **Empty or stale panels are probably not a UI bug.** If a panel shows nothing while
   the backend has data, flag it as the known stale-frontend/IPC pattern and keep it
   on a separate list -- don't restyle around it.
5. **Cleanup means less.** Prefer deleting, merging, and unifying over adding. Every
   new control has to justify itself under R7.

## How to run the session

**Phase 1 -- Tour (you drive, I narrate).**
Walk me screen by screen: Conversation, Harness, Library, Settings, the visualiser,
detached panels, modals. For each one, screenshot it, then ask me:
- What do you actually use here?
- What confuses or annoys you?
- What would you never miss if it vanished?
Write my answers into a running list as we go. Add your own observations (spacing,
alignment, inconsistent buttons, raw hex colors, dead controls, duplicated actions)
but mark them as *yours* so I can veto them.

**Phase 2 -- Sort.**
Group everything into buckets and show me the table:
- **Delete** -- unused, duplicated, or dead
- **Merge/move** -- right thing, wrong place
- **Unify** -- inconsistent with tokens / type scale / spacing / button styles
- **Fix** -- broken behavior (including stale-IPC suspects, kept separate)
- **New** -- genuinely missing (should be short)
For each item: one-line description, the file(s) it lives in, rough size (S/M/L),
and your recommendation. Then ask me to confirm or cut.

**Phase 3 -- Decide the hard ones.**
For anything with real tradeoffs, grill me one question at a time (use `/grill-with-docs`
if it touches an ADR). Record locked decisions as an ADR amendment (`/adr-amend`),
not a silent rewrite.

**Phase 4 -- Slice.**
Turn the confirmed list into small, independently-landable issues (`/to-issues`):
one issue = one PR, each with acceptance criteria I could check by looking at the app.
Order them so quick visible wins come first. Tell me which ones Felix should build
via `self_dev` and which need hand-work, and why.

**Phase 5 -- Build and verify, one slice at a time.**
For each slice:
- Make the change (renderer logic in `tray/lib/*.js` using the UMD dual-mode wrapper).
- Run jest for the tray; add one small test for non-trivial logic.
- Launch the real app and screenshot before/after -- green tests alone don't count (R6).
- Update the matching topic in `tray/lib/help-content.js` if what Felix shows changed.
- Open a PR with `Closes #N`, then stop and show me before moving on.

## What "done" looks like

- Every screen has been toured and has a decided fate for every complaint.
- The hardcoded-hex / raw-font-size / raw-padding counts from ADR-0031 are lower than
  when we started (measure at the start, report at the end).
- No new menu items, routes, or hotkeys unless one passed R7.
- A short `UI-CLEANUP.md` log at the repo root records what we cut, merged, and why,
  so the next round doesn't relitigate it.

Start with Phase 1: read the ADRs, give me a 5-line summary of the current UI rules,
then launch the app and show me the first screen.
