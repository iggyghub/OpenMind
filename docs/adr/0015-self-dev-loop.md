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
