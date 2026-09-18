# FELIX-AUDIT.md -- whole-system audit campaign driver

A full-system audit of Felix (Cerebral + plugins + tray) run 2026-09-17, plus
the campaign that lands its findings. Ten findings, F1-F10, evidence below.

**Felix-built (ADR-0015 self_dev), by operator decision 2026-09-17: "Felix
should do as much as possible."** That is achievable because of S0 -- see
below. Before S0 the campaign was split Felix/Claude-Code down the middle,
because self_dev could see only 38% of `cerebral/main.py`. S0 raised that to
95.5% and collapsed the split.

Scoped 2026-09-17.

## Status: ready

## Next slice -- start here

- **Active:** S6c -- #1308
- **Model:** sonnet

## S0 -- the unlock (DONE, hand-built)

Issue #1281, PR #1282. Hand-built in Claude Code, not self_dev: it widens
self_dev's own reach, so the tool it fixes could not do it.

`_self_dev_edit` capped every file in the edit prompt at
`_SELF_DEV_PER_FILE_FRACTION = 0.4` of the budget. That guard (#758) stops one
big file crowding out others in a slice -- but applied identically to 1-file
slices, where nothing can be crowded out, and those are most slices.
`_self_dev_truncate_to_tokens` takes `text[:max_tokens*4]`, a **prefix**, not a
relevance-ranked excerpt, so everything past the cut was invisible permanently.

The budget now splits by the file count a slice actually touches.

| File | before S0 | after S0 |
|---|---|---|
| `cerebral/main.py` | 38.4% (line 3,452 / 8,530) | **95.5%** (line 8,125 / 8,549) |
| `tray/windows/main.html` | 27.0% (line 4,976) | **67.5%** (line 9,573 / 13,653) |
| `cerebral/llm/router.py` | 100% | 100% |
| `cerebral/mcp/orchestrator.py` | 100% | 100% |
| `cerebral/llm/planner.py` | 100% | 100% |
| `cerebral/memory/manager.py` | 100% | 100% |
| `scripts/launch-felix.ps1` | 100% | 100% |

`_handle_message` (4,751), `_scheduler_loop` (3,644) and `_greet` (6,941) all
moved from unreachable to reachable. Only the tail of `main.html` past line
9,573 is still cut.

**Cerebral loads `main.py` at boot. Restart Felix after #1282 merges, before
running S1 -- otherwise self_dev keeps using the old 40% cap and every
main.py slice silently sees a third of the file.**

## Pre-flight -- MUST complete before the first campaign run

1. ~~Pin a `self_dev` model / fix the context window.~~ **RESOLVED
   2026-09-17 -- no action needed.** `custom/budd-quick` declares
   `context_window: 131072` and that is correct: measured by
   needle-in-haystack the same day, 8k / 60k / 96k (server-counted 96,852)
   all recall correctly, and 128k returns the server's own
   `litellm.ContextWindowExceededError ... maximum context length is 131072
   tokens`, overshooting by one token. The endpoint was upgraded since the
   August 64k measurement (`/v1/models` also stopped 404ing; it now lists
   `hermes-agent` and `mac-mlx-qwen36-27b`, still with no context field, so
   the window must stay stored per model). `get_task_model("self_dev")`
   falling back to the active model is fine.

   Two probe findings that matter downstream:
   - **`hermes-agent` is a reasoning model** (vLLM 0.28.0). It fills
     `reasoning_content` and leaves `message.content` **null** until
     reasoning completes; a low `max_tokens` yields `finish_reason:
     "length"` with null content, which reads like an endpoint failure. So
     `_SELF_DEV_RESPONSE_RESERVE`'s 30% (~39,322 tokens) must cover
     reasoning tokens as well as SEARCH/REPLACE output. **If a slice fails
     with an empty edit, suspect reserve exhaustion before suspecting Budd.**
   - `custom/budd-code` still declares 64000 and may now understate its own
     model. Unverified, not on this campaign's path.
2. **Merge #1282 and restart Felix.** See S0.
3. **Confirm no other driver is stuck at `Status: blocked`.**
   `self_dev_campaign` writes `Status: blocked` on `tests_failed` and never
   resets it; a stale one makes the next call silently no-op.

## Queue

- [x] S1 -- #1283 -- F2: relevance threshold in `MemoryManager.recall()`
  (`cerebral/memory/manager.py`)
- [x] S2 -- #1284 -- F9: stop `launch-felix.ps1` truncating the previous run's
  logs (`scripts/launch-felix.ps1`)
- [x] S3 -- #1285 -- F3: embedding tool shortlist with lexical fallback
  (`cerebral/llm/planner.py`)
- [x] S4 -- #1286 -- F6: bound non-`chat` calls when a `chat` waiter is queued
  (`cerebral/llm/router.py`)
- [x] S5 -- #1287 -- F1: `_handle_message` chain into a dispatch table (PR #1306, hand codemod)
- [x] S6a -- #1307 -- F10: `GATE_EXEMPT` sentinel in `MCPOrchestrator.call_tool` (`cerebral/mcp/orchestrator.py` only)
- [x] S6b (HAND, PR #1310) -- all 18 bare `_orc.call_tool(` sites in `main.py` marked `capability=GATE_EXEMPT` with a reason; AST guard test added
- [ ] S6c -- #1308 -- F10: `capability=None` resolves from the tool's declared capabilities (`cerebral/mcp/orchestrator.py` + tests)
- [ ] S7 -- #1289 -- F5: plugin-registered periodic jobs
  (`cerebral/main.py` `_scheduler_loop`)
- [ ] S8 -- #1290 -- F7: one snapshot registry replacing `_greet` + `onOpen`
  (`cerebral/main.py` + `tray/windows/main.html`)
- [ ] S9 -- #1291 -- F8: extract panels out of `main.html` (first tranche)

### Ordering and dependencies -- read before reordering

**S1 first, not S2.** S2 is a `.ps1` change and this repo has **no PowerShell
test infrastructure** -- no `*.Tests.ps1` anywhere, nothing in
`cerebral/tests/` or `tests/` touches `launch-felix.ps1`. self_dev's gate is
pytest, so S2's test step is vacuously green: it proves the
clone/edit/PR/merge plumbing while verifying nothing about the change, which
is what ADR-0034 and R6 forbid relying on. S1 is pure Python and genuinely
testable, so it proves the plumbing **and** the test gate in one run.
Operator decision 2026-09-17.

**S5 (now S5a-S5e) is a hard barrier. S6, S7 and S8 all edit `cerebral/main.py`, and S5
relocates most of it.** None of them may start until S5 is MERGED to
origin/master -- not merely committed, not merely PR-opened. Starting one
early guarantees the conflict cascade described in "Known campaign-loop
bugs" below. S1-S4 are independent of S5 and of each other.

**S9 is the only slice with a reach problem left.** `main.html` is 67.5%
visible, cut at line 9,573 of 13,653. Panels defined before that line are
editable; the tail is not. Scope S9 to one panel in the visible region and
stop -- do not let it try to extract the whole file.

## Run mode

**Run S1, then STOP and reassess** (operator decision 2026-09-17). Confirm
the PR actually merged, the queue entry ticked, and `Active:` advanced to S2
before releasing the rest. Do not run with `max_slices=9` on the first pass.

`scripts/run-felix-audit.ps1` drives the loop after that checkpoint.

## Findings and evidence

F-numbers are stable; slices reference them.

**F1 -- `_handle_message` is 2,190 lines with a 159-branch if/elif chain.**
`cerebral/main.py:4751-6941`. Every IPC message type in one body sharing one
scope with `global _active_profile`. A typo'd `elif` silently does nothing.
Fix: dispatch dict, handlers as module-level `async def`. The pattern already
exists in this repo -- `_command_registry` does exactly this for voice
commands. Slice S5.

**F2 -- memory recall has no relevance threshold.** `recall()`
(`cerebral/memory/manager.py:133`) computes `distance` and never filters on
it; `_memory_preamble` (`cerebral/main.py:1829`) takes `n_results=3`
unconditionally. Ask "what time is it" with 3 memories stored and all 3 are
injected. Distances are logged at debug level and discarded. Slice S1.

**F3 -- tool selection is bag-of-words lexical overlap over 311 tools.**
`shortlist_tools` (`cerebral/llm/planner.py:197`): words >=4 chars,
name-match x3, top-30. "Find me somewhere to eat tonight" scores zero against
every tool. The code names its own ceiling: `ponytail: lexical overlap;
upgrade to embedding recall if misses show up`.

The registry is **311 tools across 75 plugins** (measured 2026-09-17). The
docstring's "~200 tool schemas ... ~19k tokens" is stale; scaled, the full
schema set is ~29k tokens. Worse, `_build_tool_catalog(all_tools)`
(`cerebral/llm/planner.py:283`) puts the **full 311-tool** name+description
catalog in every system prompt at ~15 tokens/tool -- about 4.7k tokens -- on
top of 30 full schemas, before the user's words, the memory preamble, or 8
turns of history. On an 8k local model there is nothing left. Slice S3.

**F4 -- self_dev is one blind shot, not an agent loop.** `_self_dev_edit`
(`cerebral/main.py:3105`): ask for a file list, truncate those files, one
`complete()` call, apply search/replace, commit. No iteration, no test output
fed back, no retry on a missed SEARCH anchor, no way to read a file it
discovers it needs. **Deliberately NOT queued.** S0 fixed its budget, which
was the cheap half; rebuilding it as a real agent loop (repo map, reflection,
architect/editor) is the expensive half, and having self_dev apply it to its
own edit step is the one failure that takes the loop down mid-campaign. File
it separately once this campaign completes.

**F5 -- four background loops, hardcoded jobs, no registration seam.**
`_scheduler_loop` (`cerebral/main.py:3644`, 278 lines) hardcodes every
background job and string-matches event titles
(`IPO_CALENDAR_EVENT_TITLE`, `DISCOVERY_EVENT_TITLE`), calling
`list_due_events()` three times per tick. Four independent loops exist at
process level -- `_scheduler_loop`, `_rss_poll_loop`, `_heartbeat_loop`,
`_worker_heartbeat_loop` -- despite R5 saying the scheduler is singular.
Slice S7.

**F6 -- admission control has chat priority but no preemption ceiling.**
`_DomainSemaphore` (`cerebral/llm/router.py:177`) correctly sorts `chat`
waiters to the front. But a background call already holding the slot runs to
completion, and self_dev uses a 300s timeout -- a live voice turn can wait
five minutes with the UI showing "thinking". R5 forbids preemption, so this
is by design, but the design has no escape hatch. Slice S4.

**F7 -- two parallel hand-maintained snapshot lists.** `_greet`
(`cerebral/main.py:6941`, 18 state builders) and `main.html`'s `onOpen`
(~13 pull requests, `tray/windows/main.html:7085`) must stay in sync, plus 6
`setInterval` polls and per-tab `call_tool` fetches. There is no
`latestByType` cache in the renderer, so a state event arriving before its
panel mounts is lost. A new panel must be added in the right one of four
places; when it isn't, the panel shows stale/empty with no error. This is the
root cause of the recurring stale-panel bug. Slice S8.

**F8 -- `main.html` is 13,652 lines.** 5,356 CSS, ~7,000 inline script; 269
`getElementById`, 73 `innerHTML`, 55 event-type branches. `tray/lib/` has 32
extracted modules -- the pattern works, it just stopped. Slice S9 (first
tranche only).

**F9 -- ADRs 0032-0037 are Accepted and unbuilt while the pain they describe
is worked around by hand.** ADR-0032 documents that `Start-Process
-RedirectStandardError` truncates the crash log on every restart, and counts
37 hand-made copies as its evidence. `scripts/launch-felix.ps1:218` still
truncates. Two more `.bak` copies were made on 2026-09-17. Issues #1123-#1136
all open. Slice S2.

**F10 -- the gate is opt-in and ~15 call sites opt out.**
`MCPOrchestrator.call_tool(name, args, capability=None)`
(`cerebral/mcp/orchestrator.py:651`) skips the gate entirely when `capability
is None`, which is the default. The chain path is safe (`ChainEngine` calls
`gate_fn` first) and the tray path is safe (`_dispatch_tray_call_tool` gates
explicitly), but ~15 sites call it bare -- including `_scheduler_loop` firing
`self_dev_campaign` autonomously (`cerebral/main.py:3745`) and
`_rss_poll_once` (8326). ADR-0028 R4 says the gate is the only permission
model with no side doors. Slice S6.

**Meta-observation.** ADRs 0032-0037 were accepted in one week in September
and none have shipped, while the last three weeks of commits are ~90%
trading. The system is generating decisions faster than it retires them, and
the daily-driver defects -- log truncation, stale panels, restart races --
are the ones sitting unbuilt. R2 says the third repeat earns a mechanism;
those `.bak` files are on repeat 39.

## Open-source mechanisms worth stealing

Surveyed 2026-09-17. Each row is a mechanism Felix lacks, not a tool to adopt.

| Project | Mechanism | Applies to |
|---|---|---|
| Aider | **Repo map** -- tree-sitter extracts signatures, PageRank ranks by task relevance, fits a token budget. Replaces "truncate the files you guessed at" | F4, highest-leverage |
| Aider | **Reflection loop** -- failed edit, feed the real error back, retry | F4 |
| Aider | **Architect/Editor split** -- strong model plans in prose, weak model only applies edits. Felix already has per-task model pins; a routing change | F4, cheap |
| OpenHands | **One event stream** -- every action and observation through a single append-only replayable log. Felix has two unjoined records; ADR-0032 names this defect | F7, F9 |
| Letta/MemGPT | **Sleep-time compute** -- a second agent consolidates memory during idle, so recall quality is not paid for on the live turn. Felix has idle detection (`user_idle_ms`) | F2, post-v1 |
| Letta | **Memory blocks** -- a small always-in-context block the agent edits directly, distinct from vector recall | F2 |
| Goose | **Recipes parameterised and shareable.** Felix's are replay-only | existing feature |
| Cline/OpenHands | **Plan/act split surfaced in the UI** -- user sees and edits the plan before execution | UI |
| AG-UI | **Typed agent-to-UI event protocol** -- streamed diffs, pause/approve/edit/retry mid-flow without losing state. Felix's WS envelope is untyped ad-hoc JSON | F7 |

UI ideas from the same survey, none queued:

1. **Trajectory view.** OpenHands ships a standalone trajectory visualizer.
   Felix has the data (`conversation_turns` with
   `tool_call`/`tool_result`/`activity` kinds, plus `run_id` in the step
   ledger) and renders it as a flat chat log. Read-only view over existing
   tables; cheapest real UI win available.
2. **Cost/latency per step, inline.** `usage_totals()` exists in the router
   and surfaces nowhere.
3. **Plan preview before execution.** Chain step N+1 greyed with an edit
   affordance before it fires. Turns the gate from a yes/no modal into a
   steering surface.
4. **Approval batching.** One modal per irreversible call today; the ACL's
   once/session grants already model the batch internally, unexposed.
5. **Memory browser showing distance.** S1 needs a calibration surface
   anyway; showing which facts matched and how closely makes the threshold
   user-tunable instead of a constant.

## Known campaign-loop bugs -- hand-check between slices

1. Older `run-*.ps1` runners had a dead Status/Model regex, so `done` /
   `blocked` never auto-stopped the loop. `scripts/run-felix-audit.ps1` uses
   the repaired `Get-DriverField` pattern from
   `scripts/run-cross-stock-followups.ps1`, verified against this file.
2. "succeeded" means exit 0, **not merged**. Verify each PR actually merged
   before ticking. This is what causes conflict cascades -- see the S5
   barrier above.
3. `self_dev_campaign` writes `Status: blocked` on `tests_failed` and never
   resets it. Reset by hand after any hand-verified landing.
4. A self_dev PR's gh-visible diff can look like a full reimplementation when
   `origin/master` is stale. Verify via the sandbox clone's own `git log`
   before trusting it.

## Landed PRs

- PR #1282 -- S0 (hand-built, the unlock)

- PR #1292 -- S1 (self_dev-built; blocked on tests_failed, hand-calibrated and
  merged by hand -- see below)

S1 note: Felix's implementation was exactly as specified. The
`MAX_RECALL_DISTANCE = 1.0` the issue prescribed was an unmeasured guess and
cut real matches ("where do I live" -> "I live in Berlin" = 1.001), failing 5
existing memory tests. Calibrated to 1.63 from a measured distribution
(related 0.754..1.585, unrelated 1.678..1.983). The other 2 failures in that
run were the known #1274 full-suite pollution, not S1's. Lesson for the
remaining slices: **do not put an unmeasured magic number in an issue body** --
self_dev implements it literally and exactly, which is what it should do.
- PR #1295 -- S2 (auto-merged by self_dev_campaign)
- PR #1297 -- S3 (self_dev-built; tests_failed, hand-repaired and merged by hand)

S3 note: Felix's first cut failed 7 tests in its own files. It ran the embedding
path before the passthrough guards, kept a cwd-relative Chroma client created at
import, keyed the cache by list position, and its fallback test patched the wrong
symbol. Hand-repaired: lazy persistent index at `data_dir()/tool_index`, keyed by
tool name and re-embedded only when a tool's text changes; embedding runs after the
guards and is skipped when `prefer_web_path` reordered tools (ADR-0016 S7). Measured:
embedding all 311 tools costs ~21s on CPU. That is a one-time cost, but it lands on
the **first live turn after restart** (index empty) -- follow-up: warm the index at
boot. `test_sandboxed_eval.py::test_workdir_is_cleaned_up_after_a_run` fails on
master in isolation (stray dir under Public/OpenMind-sbx/trading): not S3's, not a
#1274 flake, unfixed.

- PR #1298 -- S4 (self_dev-built; tests_failed, hand-repaired and merged by hand)

S4 note: the issue as first written asked for an unspecified timeout on every
non-chat call, which would have killed self_dev's own 300s calls; rewritten so the
bound starts only when a chat waiter queues (`_NONCHAT_GRACE_S` = 30s policy value,
env `NONCHAT_GRACE_S`). Felix's first cut passed a bare coroutine to `asyncio.wait`
(TypeError on every non-chat call through a capped domain), did `raise ... from task`,
and its tests leaked an unrestored `pytest.MonkeyPatch()`. Hand-repaired; full suite
green (5892 passed, sandbox test deselected).

S5 note (2026-09-18): the single-shot S5 (#1287) failed with "Edit step produced
no commit" -- a 2,190-line restructure exceeds self_dev's one-call edit budget (edit
prompt ~55k tokens; response reserve ~39k shared with hermes-agent reasoning). Split
into S5a-S5e (#1299-#1303), ~440 source lines each; #1287 is now the umbrella and is
closed when S5e merges. **S6/S7/S8 need S5e merged, not S5a.**

S5a first attempt (PR #1304, closed): Felix wrote only the new test file and no
`main.py` edit. Root cause: self_dev splits the edit budget evenly across the files a
slice names (S0), so a 2-file slice (main.py + tests) shows main.py at ~50% and
`_handle_message` (line ~4770) is outside the excerpt -- no anchor to edit, and the
run still "committed" the test file so it read as a test failure. **Rule: a slice that
must edit `main.py` may name `main.py` only.** Enabling change hand-built (S0
precedent): the `_MESSAGE_HANDLERS` scaffold in `main.py` + `cerebral/tests/test_message_dispatch.py`
(516 tests calling `_handle_message` green). Issues #1299-#1303 rewritten to
main.py-only; **re-running a slice replays the old failure**: campaign run_ids are label-derived, so
S5a's ledger rows (edit/test/pr) from the closed attempt had to be cleared with `StepLedger().clear(run_id)` and the
clone moved aside before a retry -- check this after any failed slice. The exact-150-types freeze test is added by hand after S5e.


- PR #1306 -- S5 (HAND-built AST codemod; Felix's S5a/S5 attempts failed, see note)

S5 outcome (2026-09-18): three Felix attempts failed for three different reasons, all
now known. (1) single-shot: 2,190-line restructure exceeds one edit call. (2) S5a with
main.py + a test file: the even budget split showed main.py at ~50%, `_handle_message`
out of view; the run committed only the test file. (3) S5a main.py-only: model emitted a
delete block for 21 branches but its handler-insert block silently failed to apply --
**self_dev's SEARCH/REPLACE applier drops non-matching blocks silently, so a half-applied
edit can delete behaviour**; tests caught it. Landed instead as a deterministic AST
codemod (150 handlers, bodies verbatim, `git diff -w` = headers only), full suite 5897
green, verified live over IPC after restart. Issues #1299-#1303 closed as superseded.
Rule for the rest: **main.py slices must be small, single-purpose and main.py-only; a
bulk verbatim move is a codemod job, not an LLM edit job.**

S6 plan (2026-09-18): the original S6 spans orchestrator.py + ~15 main.py call sites and
must not land half-done. Split into S6a (orchestrator: sentinel, behaviour-identical),
S6b (hand: mark all bare sites `GATE_EXEMPT`, behaviour-identical), S6c (orchestrator:
flip `None` to gate). Every step is green and independently landable; the *policy*
question of which exempted sites should really be gated (scheduler `self_dev_campaign`,
RSS poll) is deliberately left as a follow-up, not decided by this campaign.

- PR #1309 -- S6a (self_dev-built; auto-merge blocked by a spurious `tests_failed`, hand-verified and merged by hand)
- PR #1310 -- S6b (hand-built)

S6a note: Felix's diff was exactly as specified. Its run reported `tests_failed` although
the captured pytest summary read "5937 passed"; re-running `self_dev_io.test_fn` on the same
clone returned PASSED=True (783s), so the verdict is not reproducible. Suspect a transient
non-zero pytest exit under load (Felix runs its trading scheduler concurrently and
`test_sandboxed_eval::test_workdir_is_cleaned_up_after_a_run` diffs a shared dir, so it is
environment-flaky). Merged by hand. S6b note: 16 tests failed on first full run -- test
fakes for `call_tool` didn't accept the new `capability` kwarg (15) plus one flaky browser
test; fakes fixed, full suite 5903 passed exit 0. **S6c may now run** (Felix restart needed:
main.py changed on master). Still open, deliberately not decided by this campaign: whether
the scheduler `self_dev_campaign`, RSS poll and `_send_channel_reply` exemptions should become
real gates (grep `gate-exempt:` in main.py).

## Explicitly NOT in this campaign

- **F4** -- self_dev as a real agent loop. See its finding above for why.
- New features. Every finding is a defect or structural constraint present in
  the code as of 2026-09-17.
- The trading subsystem. Untouched by this audit.
- Re-litigating ADRs 0032-0037. They are Accepted; S2 lands a piece of
  0032's core defect and the rest keep their own issues.
- The remaining ~32% of `main.html` past line 9,573. Out of self_dev's reach
  even after S0; needs Claude Code or a further decomposition slice.
