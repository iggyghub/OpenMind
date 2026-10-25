# UI cleanup -- running list

Marks from mark mode (Ctrl+Shift+M) plus tour observations. Sorted in Phase 2
into Delete / Merge-move / Unify / Fix / New. "(Claude)" = my observation,
the user can veto it.

## Baseline (2026-10-22)

| Window | hex | font-size | padding | gap | buttons |
|---|---|---|---|---|---|
| main.html | 723 | 361 | 284 | 131 | 140 |
| visualiser.html | 32 | 0 | 3 | 3 | 2 |
| irreversible-modal.html | 28 | 9 | 8 | 2 | 2 |
| detached-panel.html | 9 | 10 | 7 | 6 | 0 |

## Marks

### Library > Memory -- DONE (branch ui-delete-x; Recipes' delete still text, it's a panel-spec action)
1. **Delete a whole category** -- a delete control on the category header
   (`.mem-group-header`) that removes the category and all its items.
   Renderer-only: one `delete_memory` per item already exists; needs a confirm
   (22 items in one click). Size S.
2. **Every delete is a red x** -- user rule, app-wide. Today: 4 "Delete" + 1
   "Remove" text buttons, three different danger reds (`--color-danger`
   #c44b4b, `.int-btn.is-danger` #e0855a, `.cred-btn-danger`). One shared
   delete-button style on `--color-danger`. Size M (Unify).

### Log -- DONE (branch ui-delete-x)
3. **"A bunch of nonsense"** (user mark) -- entries were raw JSON. Now one
   readable line per entry (title, site/PR#, reason dimmed to 2 lines), never
   JSON. 3+ identical entries in a row fold into one expandable line (226 of
   the last 300 were "Strategy idea rejected").
4. Oldest-first -> newest-first. (Not stale: newest real entry is 10/17.)
5. The Log had no CSS at all (16px white, flush to the edge) -- styled on tokens.
6. Bug: every live `activity_turn_emitted` REPLACED the whole list with one
   entry. Now appends.
7. Bug: timestamps are SQLite UTC but were read as local time. Fixed.

### Settings -- DONE (branch ui-delete-x)
9. **Recheck folds Model priority** (user mark) -- section headers now ignore
   clicks on controls inside them. Fixed in SectionCollapse for all headers.
10. **OpenClaw row + sub-groups collapsible** (user mark) -- OpenClaw row and
    Services categories are <details> folds, state remembered.
11. **Sign-in cards -> line that morphs into a tile** (user mark) -- new
    tray/lib/tile-dialog.js; drafts kept, not submitted; Connect opens the tile.
    Follow-up idea: the Alpaca "Not set" badge sits in the title, could move
    to the status slot on the right.
12. **"Keep sections expanded" removed** (user mark) -- toggle + the whole
    force-expand override deleted.

### Header
8. (Claude) Mic control "Disabled" next to state pill "PASSIVE" reads as a contradiction.

## Stale-frontend / IPC suspects (kept separate)
- **Poll before connect** -- FIXED (branch ui-delete-x): onOpen re-runs activateRoute + the workspace panel refresh. Found 2026-10-22: when a pane is the route at
  load, its on-open request (`activity_poll`, likely others) fires before the
  WebSocket is up; `sendEvent` returns false, nothing retries, and the pane
  sits on "Loading..." forever. A likely root cause of the recurring
  stale/empty-panel problem: re-run the active route's on-open fetch once the
  socket connects.
