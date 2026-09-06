# ADR-0033: The supervisor is the tray -- restart, code load, and recovery

**Date:** 2026-09-05
**Status:** Accepted (grill session)
**Relates:** ADR-0015 (self-dev loop, SD-3 boot self-check -- refined here),
ADR-0029 (boot menu, degraded boot), ADR-0032 (process logs), ADR-0028
(R1's last rung, R6).

## Context

Felix is two processes: Cerebral (Python, `ws://localhost:7766`) and the tray
(Electron). The layer that starts them, watches them, restarts them and decides
whether a restart went well has real behaviour and no owner -- it is spread
across two files under two names, and nothing in the domain language pointed at
it. ADR-0028's resolution named it as one of three things left undecided; this
is that grill.

What exists, and where:

| Behaviour | Lives in |
|---|---|
| Prerequisite checks, cold start, 120s wait for `:7766` | `scripts/launch-felix.ps1` |
| Reconciling a half-running system (#521) | `launch-felix.ps1` sections 1-4 |
| Single-instance lock | `tray/main.js` `requestSingleInstanceLock` |
| Reconnect loop (3s, unbounded) | `tray/main.js:115` |
| Restart, in three variants | `tray/main.js` `_relaunch` / `restartFelix` / `restartFelixSelfDev` |
| Respawning Cerebral alone | `tray/main.js` `respawnCerebral()` |
| Boot self-check + SHA rollback | `tray/lib/boot-check.js` (SD-3, #556) |
| Master-update poll (5 min) | `tray/main.js` `_checkForMasterUpdate` (#817) |

**Four defects, and three of them already have a human standing in for the
missing behaviour.** That is the tell ADR-0032 was found by: a standing
workaround in `CLAUDE.md` is a design gap wearing a hat.

| Defect | Evidence | The human doing the system's job |
|---|---|---|
| Cerebral dying is never recovered | `tray/main.js:115` reconnects forever; `respawnCerebral()` has exactly one caller, `--felix-restart` in argv | double-click the shortcut |
| One wire message, two intents | `main.py:426` (the `restart_felix` tool) and `main.py:3183` (self-dev loading a PR) broadcast byte-identical `{"type": "restart_felix"}` | *"commit immediately, restarts can wipe uncommitted edits"* |
| The rollback discards uncommitted work | `boot-check.js` `gitResetFn` = `git reset --hard <sha>` on the live tree | the same instruction |
| A local commit reads as an update | `checkForUpdate` compares `HEAD` to `bootSha`; a local commit moves `HEAD`, the `merge --ff-only` no-ops successfully, and the poll restarts down the armed path | none -- this one went unnoticed |

The second and fourth compound: committing to this repo triggers an armed
restart within five minutes, and the documented defence against armed restarts
is to commit.

The launcher already learned half of the first defect. #521 taught it to handle
*Cerebral alive, tray dead*. The likelier direction -- Cerebral is the process
loading Kokoro, ChromaDB and 50+ plugins, and the one that takes tracebacks --
has no automatic path at all.

## Decision

**1. The tray is the supervisor.** Not a new component: the role was always
its, unnamed. It is the only process that is long-lived, knows whether Felix is
idle (`felixState`), can put a notification in front of the user, and is not the
thing likely to be broken -- the same reasoning that put the boot self-check in
the Electron layer rather than in Cerebral's Python. `launch-felix.ps1` is the
supervisor's **cold-start half**, not a second supervisor: it runs once and
exits, and cannot observe anything afterwards.

**Nothing supervises the tray, deliberately.** That rung is the human
double-click -- ADR-0028 R1's last rung, which outranks an unreliable
automation. A watchdog for the watchdog is a third process to keep alive.

CONTEXT.md's "registered as an OS service (Windows service / launchd / systemd)"
is re-scoped by this: a service buys **autostart at boot**, which is worth
having, and nothing else. It cannot read `felixState` and cannot notify.

**2. Arm on code load, never on restart.** The rollback belongs to *loading new
code*, not to *rebooting*. The broadcast carries the intent:

```
{"type": "restart_felix", "data": {"reason": "self_dev_load" | "user"}}
```

The tray pins, snapshots and boots with `--felix-self-dev-boot` only on
`self_dev_load`. **A missing `reason` means a plain restart**, so the
destructive path is opt-in rather than the fallthrough -- old callers and test
doubles degrade to the safe reading. The master-update poll keeps arming: it
genuinely is loading code, and #817 reasoned that correctly.

Rejected alternative: two message types (`restart_felix` / `load_new_code`). It
forks the handler and every test double for a distinction one field carries.

**3. A rollback is never destructive.** `_doRollback` stashes an uncommitted
working tree (`git stash push --include-untracked`) before resetting, and the
notification names the stash. Recovery of a human's in-flight edits becomes
`git stash pop`. "Git history is the backup" is true of commits and false of
everything else -- which is exactly the state a rollback lands on.

Rejected alternative: refuse to roll back when the tree is dirty. It protects
the work and leaves Felix wedged in the broken code that failed its check,
trading a recoverable problem for an unrecoverable one.

`_doRollback` also clears `pending_backup` when it completes. It does not today,
so the state file claims a boot check is pending long after one finished.

**4. Cerebral is respawned once, not supervised into a crash loop.** On an
unexpected disconnect (not `isQuitting`), the existing reconnect loop counts
consecutive failures; after ~5 (15s) the supervisor calls the
`respawnCerebral()` that is already written, **once**. If that does not take, it
notifies and stops. A supervisor that respawns forever turns a crash into a
crash loop, which is worse than a dead Felix because it destroys the evidence
(ADR-0032 retains one process log per launch, ten deep -- an unbounded respawn
loop rolls the actual crash out of the window).

**5. Liveness is the WebSocket; health is only asked of new code.** The
supervisor's respawn decision uses the connection it already holds, which costs
nothing. `health_check` / `gate_present` stays scoped to code loads, where "did
this change break the gate" is the actual question. It is not generalised into a
periodic heartbeat -- nothing would act on an answer the connection does not
already give.

**6. Only upstream commits are an update.** `checkForUpdate` restarts when
`HEAD` differs from `bootSha` **and** `HEAD` is an ancestor of `@{u}`. Code that
arrived from upstream is an update to adopt; code authored on this machine is
the developer's working state. This preserves every case #817 wanted (a PR
merged on GitHub and fast-forwarded, a manual `git pull`) and removes the one it
did not intend: your own commits restarting Felix.

## Consequences

- The supervisor becomes a nameable thing with one owner, so the next question
  about restart behaviour has a file to go to. It costs little to build -- every
  mechanism above already exists in the tray; four of the six decisions are
  corrections to wiring, not new machinery.
- Restart stops being a word that means two things. The tray menu, the File menu
  and the `restart_felix` tool all become the same, safe operation.
- Working on this repo stops fighting the product. Committing no longer queues
  an armed restart, and an armed restart no longer eats uncommitted work.
- A crashed Cerebral recovers without the user knowing it crashed -- the first
  behaviour in this system that makes the two-process split invisible.
- The gap that stays open by choice: if the tray itself dies, Felix is gone until
  a human clicks. Accepted, stated, and cheaper than a third process.
- Per ADR-0028 R6, none of this ships until it is exercised live: a killed
  Cerebral seen to come back, a chat "restart yourself" seen not to arm, a dirty
  tree seen to survive a rollback.

## Open

- **A crash-loop budget across boots.** Decision 4 bounds respawns within one
  tray process. A brain that boots, connects, and dies 30 seconds later, every
  time, is a slower loop the counter never sees. Deferred: it needs persisted
  state, and it has never been observed.
- **Autostart at boot**, which decision 1 re-scoped the OS-service line down to
  and did not schedule. Genuinely separate from supervision.
