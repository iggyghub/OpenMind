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

- **Active:** HELP1 -- #1045
- **Model:** sonnet

Fresh campaign, nothing landed yet.

## Queue

Strict chain: each slice adds the thing the next one calls. Do not skip ahead.

- [ ] HELP1 -- #1045 -- `help` route + nav button + empty pane shell with its own tab prefix
- [ ] HELP2 -- #1046 -- `tray/lib/help-panel.js` pure render/search functions + jest tests
- [ ] HELP3 -- #1047 -- `tray/lib/help-content.js` -- encyclopedia topics 1-9 + the authoring header
- [ ] HELP4 -- #1048 -- wire the Guide sub-tab into main.html; federated-search provider
- [ ] HELP5 -- #1049 -- encyclopedia topics 10-19 (capabilities, safety, maintaining this guide)
- [ ] HELP6 -- #1050 -- Capabilities sub-tab, rendered live from the plugins:list snapshot
- [ ] HELP7 -- #1051 -- `docs/agents/help-tab.md` update instructions + CLAUDE.md + CONTEXT.md

## Landed PRs

(none yet)

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
