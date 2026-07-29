---
name: diagnose
description: Disciplined diagnosis loop for hard bugs and performance regressions -- reproduce, minimize, hypothesize, instrument, fix, regression-test. Use when the user says "diagnose this" / "debug this", reports something broken/throwing/failing, or describes a performance regression.
kind: procedure
tools: [run_command, read_file, search_files, git_log, git_diff]
---

# Diagnose

A discipline for hard bugs. Skip a phase only when explicitly justified to the
user. Use the project's domain vocabulary (CONTEXT.md, ADRs) to build a mental
model of the modules involved before guessing.

## Phase 1 -- Build a feedback loop

This is the skill; everything else is mechanical. A fast, deterministic,
agent-runnable pass/fail signal for the bug is what lets bisection,
hypothesis-testing, and instrumentation actually work. Without one, no amount
of reading code will find the cause. Spend disproportionate effort here.

Ways to construct one, roughly in order of preference:
1. A failing test at whatever seam reaches the bug (`run_command` a test runner).
2. A CLI invocation with a fixture input, diffing output against a known-good snapshot.
3. Replay a captured trace -- save a real request/payload/log to disk, replay it in isolation.
4. A throwaway harness: the minimal subset of the system that exercises the bug via one call.
5. A property/fuzz loop -- run many random inputs, look for the failure mode.
6. A bisection harness across commits (`git_log` for candidate commits, `run_command` to drive `git bisect run`).
7. A differential loop -- same input through old vs. new code/config, diff the outputs.

Once you have a loop, iterate on it: make it faster (skip unrelated init,
narrow scope), sharpen the signal (assert on the exact symptom, not "didn't
crash"), and make it more deterministic (pin time, seed RNG, isolate
filesystem/network). A 2-second deterministic loop is worth far more than a
30-second flaky one.

For non-deterministic bugs, the goal is a higher reproduction rate, not a
clean repro -- loop the trigger, parallelize, narrow timing windows.

If you genuinely cannot build a loop: stop, say so explicitly, list what you
tried, and ask the user for access to a reproducing environment, a captured
artifact (log dump, screen recording with timestamps), or permission to add
temporary instrumentation. Do not proceed to Phase 2 without a loop you
believe in.

## Phase 2 -- Reproduce

Run the loop. Confirm the failure matches what the **user** described (not a
different, nearby failure), is reproducible across runs (or at a high enough
rate for non-deterministic bugs), and that you've captured the exact symptom
so later phases can verify the fix.

## Phase 3 -- Hypothesize

Generate 3-5 ranked hypotheses before testing any of them -- a single
hypothesis anchors on the first plausible idea. Each must be falsifiable:
"If X is the cause, then changing Y makes the bug disappear / changing Z
makes it worse." If you can't state the prediction, the hypothesis is a vibe
-- discard or sharpen it. Show the ranked list to the user before testing;
they often have domain knowledge that instantly re-ranks it.

## Phase 4 -- Instrument

Each probe maps to one hypothesis; change one variable at a time. Prefer a
debugger/REPL over logs where available. When you must log, tag every debug
line with a unique prefix (e.g. `[DEBUG-a4f2]`) so cleanup is a single grep.
For performance regressions, measure first with a timing harness or profiler,
then bisect -- logs are usually the wrong tool.

## Phase 5 -- Fix + regression test

Write the regression test **before** the fix, but only if a correct seam
exists -- one that exercises the real bug pattern as it occurs at the actual
call site. A shallow seam (single-caller test for a bug that needs multiple
callers) gives false confidence. If no correct seam exists, that absence is
itself a finding -- note it.

If a seam exists: turn the minimized repro into a failing test there, watch
it fail, apply the fix, watch it pass, then re-run the Phase 1 loop against
the original (un-minimized) scenario.

## Phase 6 -- Cleanup + post-mortem

Before declaring done:
- [ ] Original repro no longer reproduces (re-run the Phase 1 loop)
- [ ] Regression test passes (or the missing-seam finding is documented)
- [ ] All `[DEBUG-...]` instrumentation removed
- [ ] Throwaway prototypes deleted or clearly marked
- [ ] The confirmed root cause is stated in the commit/PR message

Then ask: what would have prevented this bug? If the answer is architectural
(no good test seam, tangled callers, hidden coupling), say so explicitly as a
follow-up recommendation -- after the fix lands, not before.
