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
