# BASE-DESIGN-SYSTEM.md -- baseline UI requirements, enforced by a recurring scan

The "package" a standing background loop (plugins/scheduler.py's
`ensure_design_system_event`, `cerebral/design_system.py`) and self_dev both
read from. Baseline behaviors every OpenMind screen should have -- not
per-feature decisions, not speculative (ADR-0028 rule 2: promote on the third
repeat, no pre-building) -- each entry here already showed up 3+ times in the
UI before earning a rule.

Every ~24h (`__base_design_system_scan__` recurring event), Cerebral scans
`tray/` against the rules below. A new gap files one GitHub issue per rule
(grouping every current instance into one ticket) and queues it here; if
`design_system_autofix_enabled` is on (Settings, default OFF), it then drives
`self_dev_campaign` against this file exactly like any other campaign driver
-- clone, fix, test, PR, auto-merge. With it off, gaps just accumulate in the
Queue below for a human to review and run by hand
(`self_dev_campaign({driver_file: ".../BASE-DESIGN-SYSTEM.md"})`).

## Status: ready

## Baseline requirements

1. **Scrollable + reorderable tab strips** (`tab-strip`, 2026-09-08) -- any
   horizontal row of tab-like buttons must carry the `tab-strip` CSS class
   plus a `data-tab-selector="<button-class>"` attribute, so it self-wires
   into `tray/lib/tab-strip.js`'s behavior: mouse-wheel horizontal scroll
   (down = right) once it overflows, and Shift+drag reorders the tabs among
   themselves, persisted per-strip to localStorage. No JS call needed at the
   call site -- one bootstrap loop in `tray/windows/main.html` auto-inits
   every `.tab-strip[data-tab-selector]` element on load.
   - Detector: `cerebral.design_system.check_tab_strip_compliance` -- flags a
     class ending in `-tabs` (this repo's own naming convention) missing the
     `tab-strip` class. Heuristic, not a parser: false negatives (a tab bar
     named some other way) are possible and fine, this is advisory.
   - Known exception: `#ws-tabs` (the Workspace secondary-slot's tab strip)
     rebuilds its children from scratch on every open/close and nests
     close/detach controls inside each tab -- a blind attribute patch would
     visually reorder and then snap back on the next re-render. Needs bespoke
     order-persistence wiring into its own render function; tracked by hand,
     excluded from the scan (see `_EXEMPT_IDS`).

## Next slice -- start here

(none queued -- the scan appends here when it finds a new gap)

## Queue

## Landed PRs

## SAFETY

- The scan (`cerebral/design_system.py`) is pure and read-only -- it only
  reads files under `tray/` and returns findings. Filing issues / editing
  this driver / launching self_dev_campaign are the scheduler's job
  (`plugins/scheduler.py`), kept separate so the detector stays trivially
  testable and reusable from a chat-triggered one-off check.
- `design_system_autofix_enabled` defaults OFF (mirrors `discovery_enabled`'s
  own precedent) -- the scan always runs and logs, but never files an issue
  or touches code until a human opts in via `start_design_system_autofix`.
- A rule gets filed as an issue at most once per its Queue entry; a
  regression after that entry is ticked needs a human to reopen it, same as
  any other campaign slice -- this loop does not re-file automatically.
- New rules are added to "Baseline requirements" above only after a pattern
  has shown up 3+ times across the UI (ADR-0028 rule 2) -- this file is not
  the place to speculatively pre-declare conventions nothing has needed yet.
