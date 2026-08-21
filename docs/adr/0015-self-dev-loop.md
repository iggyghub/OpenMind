# 15. The self-dev loop: Felix modifies its own core through clone → test → PR → restart

Date: 2026-07-29
Status: accepted (grill session)

## Context

Felix can gain a new *tool* (the **growth loop** / `plugins/builder.py` writes a
new **Plugin**), gain a *procedure* (a **Skill**, ADR-0014), and replay a
*chain* (a **Recipe**). It has no way to change its **own core** — the Cerebral
pipeline, the ADR-0005 gate, the tray/UI, the plugins it ships. Every one of
this repo's 500+ PRs was produced by a coding agent on a branch, gated by tests
and a PR; "Felix changes itself" is Felix driving that same loop against its own
repo.

Core self-modification breaks the two properties that make the growth loop safe:
a generated plugin is inspectable text that (a) can't touch the running brain
and (b) can't escalate past the gate. A core edit modifies the running brain and
could disable its own gate. So it is a **distinct** concept with a larger blast
radius, not a variant of the growth loop.

## Decision

1. **Two concepts, not one.** The **growth loop** (adds a Plugin; no restart —
   a new plugin loads on the next plugin scan) stays as-is. The **self-dev
   loop** is new and handles core modification. Both recorded in `CONTEXT.md`.

2. **The engine is Felix's own agentic edit loop over the model router, model
   selectable per task.** A new `task_type="self_dev"` in `cerebral/llm/router.py`
   whose model the user picks in the existing model-priority panel — local
   (Ollama), cloud (Claude), or a connected server (bonsai OpenAI-compat). We do
   **not** hardwire the `claude` CLI (it would lock the engine to one provider
   and break local-only operation). **Model quality is the user's choice; safety
   is invariant — the gate does not care which model wrote the diff.**

3. **Felix never edits the live tree in place.** A self-dev run does a full
   local **`git clone`** of the repo into the ADR-0010 shell-sandbox workdir
   (`cerebral/data/sandbox/self_dev/<run-id>/`), branches there, and the model
   edits + runs the existing test suites (pytest + the node tests) *inside the
   sandbox* — no network, resource-capped, same containment as any `shell_exec`.
   A full clone (not a shared `git worktree`) is used deliberately so the live
   `.git` is untouchable from inside the sandbox. The run produces an inert
   artifact: a branch and a **PR** via the `github` plugin.

4. **Crossing into live is a separate, gated step — merge + restart, never an
   agent file-write into the running source.** The only path from proposal to
   live is a `github` PR merge (gated) followed by Cerebral pulling `master` and
   the launcher relaunching. This mirrors every other Felix safety story: the
   sandbox emits an inert artifact; crossing into "live" is separately
   controlled.

5. **Blast-radius gate — the code analog of the zero-guessed rule.** Auto-merge
   on green tests only when the diff stays in *safe zones*: `plugins/`,
   `skills/`, `docs/`, tests, non-security tray views. Any diff touching the
   **guardrails** — `cerebral/security/` (the ADR-0005 gate), the ADR-0010
   sandbox, the credential/keyring store, `cerebral/main.py` core, or
   `plugins/self_dev.py` + the launcher rollback themselves — **always**
   escalates to a human PR review regardless of test colour. Felix may *propose*
   changes to its own guardrails but never *self-approve* them.

6. **Reversal = boot self-check + SHA rollback, owned by the launcher, not by
   Cerebral.** Before a restart-to-load: pin the current `master` SHA as
   `last_known_good` and snapshot the structured state (`openmind.db` +
   `felix-settings.json`, 5 rolling, under `cerebral/data/backups/self_dev/`).
   On relaunch the new code runs a self-check (imports OK + ADR-0005 gate present
   + IPC up on `ws://localhost:7766`); **pass** promotes the new SHA,
   **fail** resets to `last_known_good`, restores the matching state snapshot,
   relaunches the old code, and notifies. The health-check lives in the
   launcher/Electron layer (which already owns `app.relaunch`, #502) because the
   thing that might be broken *is* Cerebral. Git history is the code backup; only
   the small gitignored structured state needs snapshotting (chroma rebuilds,
   browser is cache, documents aren't corrupted by a code change).

7. **Cloud is not required, but a sandbox is.** Local and cloud models both
   drive the loop. Where no shell-sandbox backend exists the self-dev loop is
   simply unavailable (fail-closed), the same posture ADR-0010 gives
   `shell_exec`. This is a bounded, deliberate exception to design principle #3
   ("works fully offline") — a local model can run the loop, but the loop itself
   is an online-ish, deliberate activity, and the sandbox is non-negotiable.

## Considered options

- **Fold self-dev into the growth loop (one concept):** rejected — drags the
  growth loop's low-friction path up to core-edit blast radius, or drags core
  edits down to plugin-level friction. Different animals.
- **Hardwire the `claude` CLI as the engine:** rejected — it is the laziest
  agentic loop and already knows this repo, but it locks the engine to one cloud
  provider and cannot run local-only or against a connected server, which the
  user requires. The router already gives model-agnostic, per-task selection.
- **Edit the live tree in place / a `git worktree` sharing `.git`:** rejected —
  a worktree shares the parent `.git`, so a buggy git call from inside the
  sandbox could mutate the live repo. A full clone is fully isolated.
- **Copy the whole `cerebral/data/` folder as the backup:** rejected —
  duplicates what git already stores for code, and `browser/` alone is ~150 MB.
  Only `openmind.db` + `felix-settings.json` are both gitignored and
  corruptible by a migration.
- **Put the health-check/rollback inside Cerebral's Python:** rejected — a
  broken brain can't rescue itself; the rescuer must outlive the process it
  restarts.

## Consequences

- Felix gains a fourth growth axis: new tools (growth loop), procedures
  (Skills), saved chains (Recipes), and now **self-modification of core**
  (self-dev loop).
- The self-dev loop can edit its own guardrails and gate — mitigated by
  decision 5 (guardrail diffs never self-approve) and decision 6 (a change that
  breaks boot auto-reverts). These two are the load-bearing safety properties;
  slices touching them get the most review.
- A new `task_type="self_dev"` must appear in the model-priority panel, or the
  user can't pick its model.
- The launcher/Electron layer grows a boot-health responsibility it didn't have
  — the first non-trivial logic to live outside Cerebral. It must stay minimal
  (a clone can't depend on the brain to know it's healthy).

## Amendment (2026-08-21) -- campaign/slice-queue mode for self_dev

**Context** -- SD-1..4 gave Felix exactly one shape of self-dev call: a human
(or the user talking to Felix) invokes `self_dev` once per bounded
`change_description`, and gets back one PR, auto-merged or escalated. Every
*multi-slice* build in this repo (Documents, Skills, Video, the original
self-dev campaign itself, and the Book knowledge corpus campaign, ADR-0025)
instead uses an external Claude-Code loop (`scripts/run-<campaign>.ps1`)
reading a driver `.md` file and spawning one headless `claude -p` session per
slice. The 2026-08-21 Book knowledge corpus campaign surfaced the gap directly:
the user asked for it to be built by *Felix*, not by that external loop, and
there was no Felix-native way to walk an unattended multi-slice queue — only
one bounded call at a time, each requiring a human (or a Felix conversation
turn) to kick off.

**Decision**
1. `plugins/self_dev.py` gains a new tool, `self_dev_campaign(driver_file,
   max_slices=20)`, that drives the *existing* `_run()` internals (clone / edit
   / test / pr / blast-radius gate — decisions 3-5 above, unchanged) repeatedly
   against a driver file's slice queue, instead of a human calling `self_dev`
   once per slice. It is new orchestration around the proven engine, not a
   second engine.
2. **Same driver-file format** the Claude-Code loops already use (`Status:` /
   `Active: Sx -- #N` / `Model:` / `Queue` checklist / `Landed PRs`) — one
   format, read by either runner. `self_dev_campaign` parses it the same way
   `Get-DriverField` does in the `.ps1` scripts (tolerant of markdown framing),
   and rewrites it the same way step 8 of the `$rules` block does: tick the
   landed slice, advance `Active`, append to `Landed PRs`, set `Status`.
3. **Per-slice spec comes from the named GitHub issue** (`gh issue view N`),
   turned into the `change_description` passed to `_run()` — an injectable
   `issue_fn` seam, never a real `gh` call in tests, matching every other seam
   in this plugin.
4. **The loop stops, it does not retry past a human gate.** `_run()`'s existing
   `merge_decision: "escalate"` (guardrail hit or red tests) halts
   `self_dev_campaign` immediately: driver file gets `Status: blocked` with the
   escalation reason, the PR stays open, no further slices start. This is
   unchanged from decision 5 — campaign mode cannot loosen it, it only adds a
   loop around calls that individually still obey it. A `run_id` per slice
   (`campaign-<slug>-sN`) means a resumed campaign reuses the existing
   ledger-resume behaviour (issue #780) instead of re-editing.
5. **`self_dev_campaign` itself is a guardrail path.** It lives in
   `plugins/self_dev.py`, already in `GUARDRAIL_PATHS` — building this feature
   is itself a HITL slice (opens a PR, stops for human review, never
   self-merges), same posture as SD-3/SD-4.
6. **Choosing which runner to use is a human/Felix-conversation decision, not
   automated.** "Have Felix build X" routes to `self_dev_campaign`; a Claude
   Code session building Felix's own core (including this feature) still uses
   the external loop — `self_dev_campaign` cannot bootstrap the capability that
   doesn't exist yet. Nothing here retires `scripts/run-<campaign>.ps1`.

**Considered and rejected**
- **Retire the Claude-Code loop scripts, campaign mode replaces them
  entirely:** rejected — the external loop can drive `claude`'s full coding
  capability (any model Claude Code has access to, not gated by the ADR-0010
  sandbox's pytest-only test gate) and remains how Felix's own core gets built,
  including this feature itself. Two runners, one driver-file format, is
  simpler than forcing one runner to cover both cases.
- **A second, campaign-specific engine (its own clone/edit/test/pr):**
  rejected — `_run()` already is that engine; duplicating it just to add a
  loop is the second-engine mistake ADR-0011 already rejected once for a
  different capability.
- **Auto-resolve an escalation and keep looping:** rejected — decision 5's
  "Felix may propose changes to its own guardrails but never self-approve them"
  is the load-bearing safety property; a campaign loop that pushed through an
  escalation would defeat it silently, exactly where a human is least likely to
  be watching (an unattended loop).

**Consequences**
- `self_dev_campaign` is the first Felix-native consumer of the driver-file
  convention that previously existed only as a `.ps1`-script contract — the
  format is now shared infrastructure, not one runner's private parsing.
- A multi-slice campaign can be driven two ways depending on who's building it:
  ask Felix (`self_dev_campaign`, gated per-slice by the blast-radius rule) or
  run the external loop (`scripts/run-<campaign>.ps1`, gated only by tests +
  the loop author's own judgement of what counts as one slice). Both still
  produce one PR per slice with `Closes #N`.
- The 16-class capability vocabulary is unchanged; `self_dev_campaign` needs no
  capability beyond what `plugins/self_dev.py` already declares
  (`shell_exec`, `fs_write`, `fs_delete`, `network_egress_cloud`) — reading and
  rewriting a driver `.md` file is `fs_read`/`fs_write` the plugin already has,
  and `gh issue view` is the same `network_egress_cloud` class the PR step
  already exercises.

## Amendment (2026-08-21) -- in-chat pending-review card, human-click-only merge

**Context** -- decisions 4/5 correctly keep merge authority out of the model's
hands, but today the only way a human *acts* on that authority is out-of-band:
`_run()` returns `merge_decision: "escalate"` in its tool result, Felix relays
the PR URL as chat text, and the user goes to GitHub or a terminal to actually
merge it (both happened today, PR #808 and #809). The user asked directly for
this to "come up in the chat" instead.

**Decision**
1. When `_run()` escalates (guardrail hit or red tests), it also appends a
   `system_event` Conversation turn (`cerebral/db/conversation.py`,
   `KIND_SYSTEM_EVENT`) with `content = {"kind": "self_dev_pr_pending",
   "pr_url", "run_id", "branch", "reason", "test_passed"}` — the same
   structured-card shape `recipe_offer` already uses for an actionable system
   event, not a second mechanism.
2. The Main window renders that `content.kind` as a card with an "Approve &
   Merge" button. The click sends a `self_dev_pr_merge` WS IPC message,
   handled the same way `cerebral/main.py` already handles `jobs_approve_all`
   (S7 #412) — a direct dispatcher case, no LLM in the path — which calls
   `SelfDevPlugin._merge(pr_url)` then `_load(...)`.
3. **Load-bearing constraint, non-negotiable:** `self_dev_pr_merge` is reachable
   ONLY from that IPC message, sent only by the button's click handler. It is
   never registered as an LLM tool and never reachable through the planner's
   tool-calling loop — nothing in the model's context can trigger a merge,
   full stop. This is decision 5 restated for this new surface, not a new
   policy: the model proposes, the human's own click is the only path to
   merge, structurally, not by model good behavior.
4. A merge failure updates the card in place with the error (stays actionable,
   doesn't silently vanish). A merged/closed PR's card is no longer offered as
   pending on next render (checked against PR state, not just Conversation
   history, so a PR merged from GitHub directly doesn't leave a stale button).

**Considered and rejected**
- **A new "Pending Reviews" panel:** rejected for now — the user asked for
  something in the chat itself, and self-dev escalations are occasional, not
  frequent enough to justify a fifth nav destination on top of the four
  ADR-0012 deliberately narrowed to. Revisit if volume grows.
- **Exposing merge as an LLM tool gated by a model-set "confirmed" flag:**
  rejected outright — that still puts the model in the approval loop, exactly
  what decision 5 forbids. The gate must be structural (unreachable from tool-
  calling), not a policy the model is trusted to honor.

**Consequences**
- Two additions reuse existing mechanisms unchanged: `system_event` content
  vocabulary gains one more `kind` (open sub-vocabulary, same as
  `recipe_offer`); IPC gains one more direct-dispatch case, same shape as
  `jobs_approve_all`. No new mechanism class.
- `tray/` and `cerebral/main.py` are both guardrail paths (this ADR's own
  `GUARDRAIL_PATHS`) — this feature is HITL to build, regardless of which
  runner (external Claude Code loop or `self_dev`/`self_dev_campaign`) writes
  it. Its own PR does not get to auto-merge itself.
- The 16-class capability vocabulary is unchanged.

## Amendment (2026-08-21) -- full auto-merge, decision 5 and the prior
amendment's "non-negotiable" gate reversed

**Context** -- the human-click-only gate above worked as designed (used
earlier today to merge PR #808/#809), but surfaced a real cost: when a
self-dev run fixes a bug in Felix's own tool-use capability (e.g. the
`shortlist_tools` fix in #809), the fix sits in a pending-review card until
a human clicks it, so Felix can't pick up and use its own fix in the same
flow. The user weighed that against the guardrail gate's protection
(explicitly walked through — this reverses the *same-day* "non-negotiable"
decision above, including the guardrail-path list: `cerebral/security/`,
`cerebral/sandbox/`, `cerebral/db/credentials.py`, `cerebral/main.py`,
`plugins/self_dev.py` itself, `tray/`) and chose full auto-merge, on the
condition that a real backup/rollback exists. It does, independent of this
gate: `tray/lib/boot-check.js` (SD-3) pins the last-known-good SHA and
snapshots `openmind.db`/`felix-settings.json` before every self-dev restart,
and on a failed boot self-check does a `git reset --hard` + snapshot
restore + relaunch, automatically, no human action needed.

**Decision**
1. `SelfDevPlugin._run()` (`plugins/self_dev.py`) no longer branches on
   `guardrail_hit or not test_passed`. Every run reaches `_merge()` +
   `_load()` and returns `merge_decision: "auto_merge"` unconditionally
   (barring a hard error, e.g. the edit step producing no commit, or `_merge`
   itself raising).
2. `is_guardrail_diff`/`GUARDRAIL_PATHS` detection is unchanged and still
   runs on every PR — its result (`guardrail_hit`, `guardrail_reason`) is now
   purely informational: included in the tool result and, when true or tests
   failed, recorded as a `system_event` (`kind: "self_dev_pr_auto_merged"`)
   so it's visible in Conversation history after the fact. It no longer gates
   anything.
3. `self_dev_campaign` (SD-5) inherits this automatically — it loops on
   `_run()`'s `merge_decision`, which is now always `"auto_merge"`, so a
   campaign runs slice-to-slice unattended until it errors, not until it
   hits a guardrail file.
4. The `self_dev_pr_pending` card / "Approve & Merge" button / the
   `self_dev_pr_merge` IPC dispatcher case in `cerebral/main.py` are left in
   place (dead code, not removed) — nothing in `_run()` emits
   `self_dev_pr_pending` anymore, so the card never renders, but the
   dispatcher path itself is inert rather than deleted, in case a narrower
   gate is reintroduced later.
5. `plugins/self_dev.py` is itself in `GUARDRAIL_PATHS` — this change to the
   gate's own enforcement code was, ironically, made under the *old* regime
   (a human, not self_dev, wrote and reviewed this PR before merge).

**Considered and rejected**
- **Narrow carve-out (auto-merge only for tool-visibility/read-only fixes,
  keep the gate for security/sandbox/credentials/self_dev.py):** offered to
  the user as the middle option; they chose full removal instead. Revisit if
  a self-merged change to a guardrail path causes real harm the boot-check
  rollback doesn't fully undo (e.g. external side effects made before a
  crash, which a code/DB rollback can't reverse).
- **Keep the gate, fix the bootstrap problem some other way (e.g. auto-load
  a merged-but-unapproved branch into a shadow process):** not pursued —
  substantially more machinery for the same outcome the user was willing to
  accept directly.

**Consequences**
- The load-bearing claim in the prior amendment ("the human's own click is
  the only path to merge, structurally, not by model good behavior") no
  longer holds. `self_dev`/`self_dev_campaign` can now modify and merge
  changes to its own security gate, sandbox, credential store, and the
  self-dev loop itself, unattended, including with failing tests.
- The compensating control is boot-time, not merge-time: a bad self-merge is
  caught when Cerebral fails to come up cleanly after the restart `_load()`
  triggers, at which point SD-3 reverts code + DB/settings to the last known
  good state automatically. A self-merge that boots fine but is subtly wrong
  (e.g. quietly weakens a check without crashing) is not caught by either
  mechanism — this is a real gap, accepted knowingly, not an oversight.
- `GUARDRAIL_PATHS`/`is_guardrail_diff` are kept (not deleted) specifically
  so a future re-tightening has the detection logic ready to wire back into
  a blocking gate without rebuilding it.

**Update (2026-08-21, same day) -- manual rollback delivered (#813)**

The gap this amendment named above (a self-merge that boots fine but is
later found wrong) now has a partial answer: an on-demand rollback,
independent of the automatic SD-3 boot-check path, reachable two ways --
a "Roll back last self-dev change" tray menu item (with a confirm dialog,
since it's a `git reset --hard`), and a new `self_dev_rollback` tool Felix
can invoke from chat. Both call the same new `manualRollback()` in
`tray/lib/boot-check.js`, which reverts to the `last_known_good` SHA and
restores the matching `openmind.db`/`felix-settings.json` snapshot -- the
same restore `_doRollback` already did, just callable any time, not only
right after a self-dev restart (`pinAndSnapshot` now also writes a
`last_backup` timestamp that, unlike `pending_backup`, is never cleared on
a passing boot check).

Deliberately *unlike* the merge gate this ADR spent so many words making
structurally unreachable from the model: `self_dev_rollback` **is** a
planner-reachable tool. That's intentional, not an inconsistency --
decision 5's concern is the model approving forward progress on its own
guardrails; undo is the safe direction (worst case it reverts a good
change, self-correctable by running self-dev again), so gating it behind a
human click the way merge is would defeat the entire point of pairing it
with full auto-merge.

Still not covered: a bad self-merge whose damage isn't undone by reverting
code + DB/settings alone (e.g. an external side effect made before anyone
notices something's wrong). Rollback undoes *state*, not *actions already
taken*.

## Amendment (2026-08-21, same day) -- restart-storm fix + external-merge auto-restart (#817)

**Context** -- resuming the Book campaign after the full-auto-merge amendment
landed hit a real incident: S2 (#798) touched `tray/lib/book-panel.js` and
correctly auto-merged, but the tray got stuck relaunching -- two Cerebral
processes ended up fighting over `:7766` and an Electron relauncher helper
process never handed off. Root cause traced to two things:

1. `restartFelix()`/`restartFelixSelfDev()`/the boot-check rollback path/the
   new manual-rollback path all built their `app.relaunch()` argv as
   `process.argv.slice(1).concat([...new flags])` -- but a relaunched
   process's own argv already carries whatever flags the *previous* launch
   added. Concatenating instead of replacing meant repeated restarts grew
   the flag list unbounded (`--felix-restart` was observed repeated 6 times
   in the stuck instance's command line).
2. Nothing prevented two relaunch requests from firing back to back (e.g.
   two campaign slices auto-merging close together) -- a second
   `restartFelixSelfDev()` call could pin+snapshot+relaunch again before the
   first one's process had actually exited, racing for the same port.

Separately, this session also surfaced the gap the earlier "full auto-merge"
amendment didn't fully close: merging a PR directly on GitHub (as happened
for PR #812/#814, merged via `gh pr merge`, not through self_dev's own
`_load()`) leaves the *running* process on the old code indefinitely -- only
a manual restart picks it up. That's exactly what made the S2 escalation
look like a regression when it wasn't: the live process simply hadn't
reloaded #812's code yet.

**Decision**
1. `tray/lib/boot-check.js` gains `cleanFelixArgv(argv, extraFlags)` --
   strips every known felix relaunch flag (`FELIX_RELAUNCH_FLAGS`:
   `--felix-restart`, `--felix-self-dev-boot`) before re-adding exactly the
   ones this relaunch needs. `tray/main.js` funnels every
   `app.relaunch()+quit()` through one function, `_relaunch(extraFlags,
   source)`, which uses this and also refuses to fire while
   `_restartInProgress` is already true (set the moment a relaunch starts,
   never reset -- the process is dying anyway). `restartFelixSelfDev()`
   checks the same flag before it even pins/snapshots, so a duplicate call
   can't clobber the in-flight pin with a second one.
2. `tray/lib/boot-check.js` gains `checkForUpdate(...)` -- pure decision
   function (git operations injected, same DI style as the rest of this
   file): `git fetch`, compare local HEAD to `@{u}`, fast-forward
   (`--ff-only`, never force) if behind, then compare the resulting HEAD to
   the SHA this process booted with. Returns `restart` (new commits, Felix
   idle), `defer` (new commits, Felix mid-response/mid-chain -- wait for the
   next `passive` WS event before restarting so nothing gets cut off),
   `none`, or `skip` (fetch/rev-parse/merge failed -- e.g. offline, or local
   has diverged; try again next interval, never force). `tray/main.js` polls
   this every 5 minutes (`AUTO_UPDATE_POLL_MS`) and, on `restart`/deferred-
   then-idle, calls the *same* `restartFelixSelfDev()` path a self_dev
   merge already uses -- an externally-merged change gets the identical
   SD-3 boot-self-check + rollback safety net, not a bare unprotected
   restart.

**Consequences**
- Any future merge to master -- self_dev's own, or a human/Claude-Code
  session merging via `gh pr merge` -- now takes effect within ~5 minutes
  without a manual restart, and is boot-checked exactly like a self_dev
  merge would be.
- The restart-storm class of bug (unbounded argv growth, overlapping
  relaunches) is fixed at the one choke point (`_relaunch`) every relaunch
  path now shares, rather than patched per call site.
- `tray/main.js` itself still has no dedicated test file (Electron-coupled,
  consistent with the rest of this codebase) -- the actual decision logic
  for both fixes lives in `tray/lib/boot-check.js` instead, specifically so
  it stays hermetically testable; `main.js`'s job is now just wiring real
  `git`/`app` calls into it.
