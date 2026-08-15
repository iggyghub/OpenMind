# Harness-Parity Campaign — Handoff (2026-08-15)

Adopt the capabilities OpenMind lacks vs `deepseek-ai/deepseek-harness` (reviewed
2026-08-15; its docs are extracted in `openmind.db` collection 'harness improvements').
Everything below is staged on master and **held** — nothing runs until you launch it.

## Next slice — start here

1. **Active:** H5-S1 — #736 (spill store). Model: sonnet. Type: AFK.
2. **Launch:** edit `HARNESS-PARITY.md` — change `## Status: blocked` and the bare
   `Status: blocked` line to `ready`, commit to master, push.
3. **Run:** `scripts/run-harness-parity.ps1` (double-click or `& scripts/run-harness-parity.ps1`).
   It builds one slice per fresh `claude -p` session, AFK slices self-merge, HITL slices
   stop for review.

## State at handoff

1. ADRs on master: **0021** compaction, **0022** session-log+fork, **0023** workflow,
   **0024** Code Mode, plus the **ADR-0020 amendment** (subagent seam). Grills done.
2. Issues **#732–#739** (`harness-parity` label). Design-first issues have an ADR-ref comment.
3. Driver `HARNESS-PARITY.md` (Status: blocked) + `scripts/run-harness-parity.ps1` +
   `scripts/stop-harness-parity.ps1`. 11 slice-granular queue entries.
4. master @ latest (ADRs + staged campaign + the run-delegation regex fix all pushed).
5. Delegation campaign (D) is separate and paused: S1 merged (#731); S2–S4 in
   `DELEGATION-BUILD.md`.

## Build order (the driver Queue; each entry = one tracer PR)

1. H5-S1 #736 — spill store + retrieve tool — AFK — standalone.
2. H4-S1 #735 — command registry + no-LLM dispatch (main.py) — HITL.
3. H6-S1 #737 — approval presets over the ADR-0005 gate — HITL — **confirm the preset
   set first** (keep `full-auto` first-class per the "keep bypass mode" preference).
4. H1-S1 #732 — model context_window metadata + token estimator — AFK.
5. H1-S2 #732 — tool-result pruning via spill (needs H5) — AFK.
6. H1-S3 #732 — oldest-turn summarization in main.py — HITL.
7. H3-S1 #734 — derive_model_context() + assembly invariant — HITL.
8. H3-S2 #734 — fork(session, boundary) — AFK.
9. H2-S1 #733 — subagent provider seam + continuation + jobs — HITL — **only after the
   delegation campaign (D, #727–730) lands.**
10. H7-S1 #738 — task-workflow over subagents — AFK — gated on H2; **defer unless a real
    multi-step task needs it.**
11. H8-S0 #739 — Code Mode sandbox spike, cloud-gated — HITL — **last.**

## Per-slice verify (do this as reviewer)

1. AFK slice auto-merges after its pytest is green — then confirm real-not-inert: touches
   only the named files, tests exercise real behaviour (not a scaffold), no invented modules.
2. HITL slice opens a PR and sets `Status: blocked` naming the PR, then the loop stops.
   Review the PR, merge it, then in `HARNESS-PARITY.md`: tick the slice, set the next entry
   Active (its `Active:`/`Model:` lines), set `Status: ready`, commit to master, relaunch.
3. If a slice fails 3 attempts, the runner stops — use `loop-doctor` on
   `.claude/tmp/harness-parity-loop/`.

## Open design forks (defaults chosen; override only if you disagree)

1. Compaction — lazy prune-then-summarize (chosen) vs rolling summary.
2. Session-log — minimal invariant+fork (chosen) vs full event-sourcing.
3. Workflow H7 — defer until a task needs it (chosen) vs build alongside H2.
4. Code Mode H8 — accepted-but-last, cloud-only (chosen) vs wontfix-for-now.

## Gotchas (all bit us this session — do not relearn)

1. **One working tree, one session.** Two Claude sessions on this repo switch branches
   under each other (a concurrent coding-routing session left the tree on
   `feat/coding-model-routing`; commits landed on the wrong branch). Do not run this loop
   while another session/loop edits the repo. Confirm `git branch --show-current` is
   `master` before launching.
2. **The runner STOP kills only the `claude.cmd` wrapper, not the child `claude.exe`.** A
   graceful "pause" mid-slice leaves the builder running orphaned — kill it by PID:
   `Get-CimInstance Win32_Process -Filter "Name='claude.exe'" | ? { $_.CommandLine -like '*bin\claude.exe*-p*--model*' }` then `Stop-Process -Force`.
3. **A kill after merge-but-before-driver-update leaves the driver stale** (points at the
   just-merged slice). Reconcile the driver before relaunch: tick the landed slice, advance
   Active.
4. **Driver field regex is fixed** in both runners (`^[#\-\*\s]*Name...`) — it now parses the
   markdown `## Status:` / `Active:` / `Model:` lines. The old `^Status:` anchor silently
   defaulted (Status never blocked, Model always sonnet).
5. HITL = guardrail (`cerebral/main.py`, `cerebral/security/`) or a new autonomous
   capability. The dev-loop itself stays external (ADR-0023) — no Felix-run self-approving loop.

## Pointers

1. Design: `docs/adr/0020`–`0024`. Program driver: `HARNESS-PARITY.md`.
2. deepseek source review: `openmind.db` collection 'harness improvements'; repo
   `github.com/deepseek-ai/deepseek-harness` (head 47f94385).
3. Runner conventions / healing: skills `campaign-scaffold`, `loop-doctor`.
