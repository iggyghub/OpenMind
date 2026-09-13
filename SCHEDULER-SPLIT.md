# SCHEDULER-SPLIT.md -- Scheduler Trading-Tools Extraction campaign driver

`plugins/scheduler.py` is 2200+ lines under a module docstring that still says
"Tools: create_event, list_events, update_event, delete_event, run_gauntlet.
SQLite-backed. No external calendar deps." Only the first four are calendar
tools. The other ~24 tools are trading-domain (gauntlet/strategy admin, paper
trading control, discovery, book ingestion, IPO calendar) and design-system
autofix -- none of which belong under a plugin named "scheduler". Identified
during the Historical Replay campaign (REPLAY.md, decision D8) as the reason
`plugins/trading_replay.py` was created as its own new plugin instead of two
more tools bolted onto this one. Filed as #1208.

**Read before running this campaign:** every trading-domain slice landed in
this repo's history has needed a hand fix after a green test run (see
REPLAY.md's own note, and TRADING.md/TRADING-AUDIT-FIXES.md/STRATEGY-REPAIR.md
before it). This campaign is pure relocation -- no trading logic changes -- but
`cerebral/main.py` reaches directly into `SchedulerPlugin`'s private attributes
(`_settings`, `_book_store`, `_discovery_watchlist`, `_lifecycle`, event-title
constants, etc.) in a dozen places, so the wiring is the actual risk, not the
tool bodies. **Hand-verify every PR's actual diff, and hand-restart Felix to
confirm live behavior, before merging -- even on a green sandbox test run.**

## Next slice -- start here

- **Active:** S5 -- #1213
- **Model:** sonnet
- **Status:** ready

## Queue

- [x] S1 -- #1209 -- extract design-system-autofix tools (smallest, proves the pattern)
- [x] S2 -- #1210 -- extract book-library tools
- [x] S3 -- #1211 -- extract discovery tools
- [x] S4 -- #1212 -- extract IPO-calendar tools
- [ ] S5 -- #1213 -- extract strategy/gauntlet admin tools (largest slice)
- [ ] S6 -- #1214 -- extract trading-control tools (riskiest, land last)
- [ ] S7 -- #1215 -- scheduler.py cleanup: docstring, dead imports, capability narrowing (closes #1208)

Order matters: S1-S4 are increasingly-coupled but independent extractions:
each removes one self-contained subsystem and its own main.py wiring. S5 is
the biggest single tool set. S6 touches the live paper/real trading dispatch
path (`dispatch_due_events` in `cerebral/trading/live_tick.py`) and must land
last among the extractions, once the pattern is well-proven. S7 is cleanup
only, once scheduler.py genuinely holds only calendar tools.

## Landed PRs

- PR #1217 -- S1: extract design-system-autofix tools (merged 2026-09-13)
- PR #1218 -- S2: extract book-library tools (merged 2026-09-13)
- PR #1219 -- S3: extract discovery tools (merged 2026-09-13)
- PR #1220 -- S4: extract IPO-calendar tools (merged 2026-09-13)

## SAFETY

- **Before moving any method out of `scheduler.py`, grep every ALREADY-MERGED
  plugin (not just `scheduler.py` itself) for a private reach into it.** Found
  the hard way after S1-S4: `book_library.py` and `discovery.py` both call
  `self._scheduler._run_gauntlet(...)` directly -- a dependency invisible from
  reading `scheduler.py`'s own diff, since it lives in a *different* file that
  isn't part of the slice moving `_run_gauntlet` away. It wouldn't have failed
  a test either -- both plugins' test suites construct fake schedulers, so
  only the real production wiring breaks, silently, the first time a book
  claim or a discovered idea tries to run the gauntlet. #1213 (S5) was amended
  with the concrete fix (a distinct `_gauntlet` reference, not a repoint of
  `_scheduler`, since `discovery.py` still needs `_scheduler` for its calendar
  delegation) before it ran. Do this same audit before S6 and S7 too, even
  though S6 came up clean (`_on_trading_change` turned out to be an
  independent per-plugin attribute, not a shared reach).
- **`cerebral/main.py` is Cerebral's live orchestrator.** Every slice changes
  it. After each merge, hand-restart Felix (`scripts/launch-felix.ps1`, run
  directly -- never `Start-Process`-wrapped, see CLAUDE.md) and confirm via the
  IPC bridge (`ws://localhost:7766`) that the moved tools still work and any
  recurring event they own still fires. A green test run is necessary, not
  sufficient (ADR-0028 R6).
- **Every new `plugins/<name>.py` needs `cerebral/tests/test_plugin_<name>.py`**
  or `MCPOrchestrator(verify_test_files=True)` refuses it at registration with
  `REASON_NO_TEST_FILE` -- silent, boot continues, only visible by grepping
  `cerebral.err.log` for "Refused plugin". Mirror
  `cerebral/tests/test_plugin_trading_replay.py`'s pattern: a real
  `REQUIRED_CAPABILITIES` assertion plus one guard-clause test, not a
  placeholder.
- **No slice may change trading logic, gauntlet gating, or dispatch order.**
  Pure relocation of tools/methods/state and the main.py wiring that reaches
  them. If a slice's issue asks a genuine design question (S6 does, on how
  `dispatch_due_events`'s duck-typed `scheduler` arg gets satisfied), resolve
  it the way the issue specifies -- don't improvise a different shape.
- **`list_due_events()`/`mark_event_run()` and the `events` table stay on
  `SchedulerPlugin`.** They're the generic calendar dispatch seam every
  extracted plugin's recurring-event loop depends on. Nothing moves them,
  duplicates the table, or changes the schema until S7, and S7 doesn't touch
  them either.
- **`test_plugins_time_notes.py`'s `test_list_tools_exposes_all_scheduler_tools`**
  shrinks by exactly the tool names each slice moves out -- update it in the
  same PR as the move, not as a follow-up.
