# HELP.md -- self_dev campaign driver for the Help tab (Felix encyclopedia)

Source: user request, 2026-09-02. "A help tab that is like an encyclopedia/user map for
everything Felix does, how it works, so either an AI or a human can read it to better
understand what it's capable of." It must be updatable later, with instructions left behind
for whichever AI is asked to update it. Its own top-level sidebar button.

## What it is

Two halves, one pane:

1. **Guide** -- a hand-written encyclopedia of concepts: what Felix is, how a spoken request
   becomes an action, memory, permissions, plugins, skills, self-dev, trading, documents,
   jobs, computer-use, models. Static content living in ONE file, `tray/lib/help-content.js`.
   This is the half a human or an AI reads to understand the system.
2. **Capabilities** -- a LIVE index of every registered tool, grouped by plugin, with its
   description and required capabilities. Rendered from the `plugins:list` websocket payload
   the Harness pane already receives (`cerebral/main.py::_plugins_snapshot_data`). No new
   backend code, and it can never go stale.

Splitting it this way is the whole trick: the part that rots fastest (the tool list) is
generated, and the part that needs a human/AI author (the concepts) is a single flat file.

## Read before running this campaign

**This is `tray/` work, and `tray/` is in self_dev's `GUARDRAIL_PATHS`.** That no longer
blocks auto-merge (2026-08-21 amendment), but the sandbox test gate runs **pytest only and
cannot validate JavaScript at all**. A green sandbox verdict on these slices means nothing.
The only real check is:

- read the PR's actual diff by hand, and
- run `cd tray; npm test` locally (jest), and
- open the Main window and click the tab.

Do all three on every slice before merging. Do not trust `tests_passed` here.

**Slices must be small, and must not ask for several edits to `main.html` at once.**
The first HELP1 attempt (PR #1053) was told to make four changes to `main.html` and landed
only one of them -- the small self-contained CSS block -- then auto-merged anyway and broke
the jest suite. `main.html` is ~13k lines; an edit step that has to find four separate
anchors in it will quietly do the easiest one. Give exact search anchors ("insert after the
line containing X") and keep each slice to one or two insertions.

**Trap self_dev will otherwise walk into:** `.lib-tab` / `.lib-sub` are queried *globally*
by the renderer's Library code (see the comment at `tray/windows/main.html:4452`). The Help
pane must use its own `help-tab` / `help-sub` class prefix, exactly like Trading's
`.trd-tab` / `.trd-panel` and Permissions' `.perm-tab`. Reusing the Library classes will
silently break the Library pane's tab switching.

**Second trap:** every `tray/lib/*.js` loaded by a plain `<script src>` tag shares ONE
global lexical environment. New libs must be wrapped in an IIFE with the dual-mode export
footer (`module.exports` for jest, `window.X` for the renderer) -- see the long comment at
the top of `tray/lib/sidebar-router.js` for why a bare top-level `const` there is a
page-killing SyntaxError that the Node test suite cannot catch.

## Status: ready

## Next slice -- start here

- **Active:** HELP2 -- #1046
- **Model:** sonnet

HELP1 landed 2026-09-03 (PR #1058, on top of the partial #1053). self_dev's edit step twice
failed to complete this slice cleanly -- first attempt (#1053) did 1 of 4 edits, second
attempt (#1058) did 1 of 3 but malformed it (split the Log button's opening tag in half).
Both times the sandbox test gate (pytest-only, no jest) passed the PR through anyway. Hand-
finished #1058: fixed the broken nav markup, added the missing pane and click handler, added
the 7th-nav-item case to render-smoke.test.js, verified 30/30 suites locally AND by clicking
through Guide/Capabilities in a live-served copy of main.html before merging. See "Slices
must be small" below -- HELP4 as originally scoped has six main.html edits in one slice and
will need the same splitting treatment before it's fired.

## Queue

Strict chain: each slice adds the thing the next one calls. Do not skip ahead.

- [x] HELP1 -- #1045 -- `help` route + nav button + empty pane shell with its own tab prefix
- [ ] HELP2 -- #1046 -- `tray/lib/help-panel.js` pure render/search functions + jest tests
- [ ] HELP3 -- #1047 -- `tray/lib/help-content.js` -- encyclopedia topics 1-9 + the authoring header
- [ ] HELP4 -- #1048 -- wire the Guide sub-tab into main.html; federated-search provider
- [ ] HELP5 -- #1049 -- encyclopedia topics 10-19 (capabilities, safety, maintaining this guide)
- [ ] HELP6 -- #1050 -- Capabilities sub-tab, rendered live from the plugins:list snapshot
- [ ] HELP7 -- #1051 -- `docs/agents/help-tab.md` update instructions + CLAUDE.md + CONTEXT.md

## Landed PRs

- PR #1053 -- HELP1 partial (CSS + router entry only; auto-merged by self_dev_campaign
  despite being incomplete -- pytest-only sandbox gate couldn't see the missing JS/HTML)
- PR #1058 -- HELP1 completed (nav button, pane, click handler; self_dev's edit malformed
  the nav markup, hand-fixed before merge)

## Design summary (hand-review context, not part of any single issue)

**Content format, deliberately not markdown.** A topic body is a plain array of strings.
A string beginning with `- ` is a bullet; consecutive bullets group into one `<ul>`;
everything else is a paragraph. That is about fifteen lines of renderer code and zero
dependencies. A markdown library was considered and rejected -- there is no markdown
renderer anywhere in `tray/` today, and this content is authored by us, so it does not need
to survive arbitrary markdown.

**Cross-links via `see_also`** (an array of topic ids) are what make it an encyclopedia
rather than a FAQ. They render as clickable topic links.

**No `tools:` field on topics.** Tempting -- linking a concept to the tool names that
implement it -- but that is exactly the field that goes stale the day a tool is renamed,
and the Capabilities tab already answers "what tools exist". Left out on purpose.

**No backend, no database, no new websocket message type.** The Guide is a static JS file;
the Capabilities index reuses a payload the tray already receives. If a future slice wants
per-user notes or bookmarks on help topics, that is when a store gets added, not before.

**Why a top-level nav button rather than a Settings sub-tab:** the user asked for one, and
a help index is a peer of Conversation/Library/Trading, not a setting. Note this does NOT
contradict the "sub-tabs here going forward, never new top-level sidebar sections" comment
at `main.html:6418` -- that comment is scoped to *Trading* views.
