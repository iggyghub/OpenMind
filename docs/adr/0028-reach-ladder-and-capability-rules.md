# ADR-0028: The reach ladder and the capability-acquisition rules

**Date:** 2026-09-05
**Status:** Accepted
**Governs:** every capability built into Felix from here, and every agent
session that builds one.
**Relates:** ADR-0001 (MCP as the universal interface), ADR-0004 (integration
waves), ADR-0005 (security gate), ADR-0008 (core loop), ADR-0010 (shell
sandbox), ADR-0014 (Skills), ADR-0015 (self-dev), ADR-0016 (computer use),
ADR-0020 (delegation), ADR-0024 (Code Mode, rejected).

## Context

Felix is an OS in every way that matters: a syscall table (the MCP tool
registry), protection rings (ADR-0005's 16 capability classes), drivers (70
plugins), IPC (OpenClaw + `ws://localhost:7766`), a filesystem (Chroma +
SQLite + the Document library), and a shell (the conversation).

That framing exposes what is actually missing, and it is not reach.
**Everything a computer can do lands on one of five surfaces, and each already
has a universal primitive:**

| Surface | Primitive | Coverage |
|---|---|---|
| Syscalls, files, processes | `files`, `system`, `shell` | total |
| Anything with a CLI | `shell_exec` inside the ADR-0010 sandbox | total |
| Anything with an HTTP API | `http_client` + ~60 service plugins | total |
| Anything with a web UI | `browser_session` (logged-in harness) | total |
| Anything with only a GUI | `computer_use` (ADR-0016) | total |

The bottom three rungs are universal by construction. There is no computer
capability that is not a CLI, an API, a web app, or pixels. A 71st plugin
therefore buys reliability and token cost -- never new reach.

What *is* missing is arbitration. Nine mechanisms exist to gain a capability
(hand-authored plugin, `plugins/builder.py`, Skill, Recipe, `self_dev`,
`delegate`, workflow, shell, computer-use) with no rule for choosing one, and
no rule for when a capability is worth building at all. ADR-0004 sorted
integrations into *waves*; nothing sorted them by *cost*.

## Decision

Six rules. They bind at both ends -- Felix at runtime, and any agent session
building on this repo.

**R1. The reach ladder. Stop at the first rung that reaches.**

> existing tool -> API plugin -> sandboxed shell -> browser session ->
> computer-use -> ask the human

Each descent costs roughly an order of magnitude more tokens and an order of
magnitude more flakiness. A lower rung is never chosen for elegance, and a new
rung is never built while a lower one already reaches. "Ask the human" is a
rung, not a failure -- it outranks an unreliable automation.

**R2. Promote on the third repeat, never the first.** A capability reached ad
hoc (shell, browser, computer-use) twice is fine. On the third repeat it earns
a plugin. This replaces speculative pre-building: ADR-0004's "second wave" is
demand-driven, and the trigger is a counted repeat, not an intuition.

**R3. The acquisition mechanism follows what is actually missing.** One
question, one answer -- these are not interchangeable:

| Missing | Mechanism |
|---|---|
| Know-how / procedure | Skill (ADR-0014) |
| A tool | Growth loop (`plugins/builder.py`) |
| A replay of a known chain | Recipe |
| Something in Felix's own core | `self_dev` (ADR-0015) |
| Context room, not capability | `delegate` (ADR-0020) |

**R4. The ADR-0005 gate is the only permission model.** Every mechanism routes
its calls through the same 16-class gate and ACL, or it does not ship. No
mechanism gets a side door, a pre-approved list, or an "internal" exemption.
Code Mode (ADR-0024) was rejected partly on this ground; that precedent holds.

**R5. One GPU is the scheduler.** Nothing is designed assuming concurrency.
Two local sub-agents serialize on the single 8 GB GTX 1080, so parallelism is
never the justification for a design (ADR-0020 already settled this for
delegation; R5 generalises it). Where two things genuinely compete, the live
conversational turn wins.

**R6. Verified running, or it did not ship.** Green tests are necessary and not
sufficient. A capability is done when it has been exercised through its real
surface -- the live Cerebral over the IPC bridge, the actual browser, the
actual tray -- not when its unit tests pass. Precedent: the UI-editor campaign
shipped test-green code that was broken on first click-through.

## Consequences

- Capability arguments stop being taste. "Which rung?" and "what is missing?"
  are answerable from the tables above, and a proposal that skips a rung has to
  say why.
- Plugin count grows slower and by demand. Expect fewer, better-worn plugins,
  and more work done on rungs 3-5 first.
- These rules are written where they are read, not into a governance document:
  R1 enters the planner's system prompt (`cerebral/llm/planner.py`) so Felix
  picks the cheapest surface at runtime, and all six enter `CLAUDE.md` so every
  agent session inherits them. This ADR is the canonical text; both are
  pointers to it.
- R6 makes live verification a shipping requirement, which is slower per slice
  and is the intended trade.
- R2 and R6 are the two rules with teeth against the failure mode this repo
  actually has: building capability faster than it can be trusted.

## Resolution (2026-09-05 grill) -- no rewrite

The open fork below was **resolved against a rewrite.** The grill it asked for
happened the same day: an OS mapping of Felix across thirteen levels (storage,
model, tool registry, gate, kernel loop, drivers, services, IPC, package
manager, shell, toolkit, apps, user), run specifically to find structural gaps.

> **Open (the fork, now closed):** Whether this is a rewrite trigger. The rules
> describe a system that mostly already exists -- the ladder's five primitives
> are all built. What a v2 would change is the *arbitration layer* (a scheduler,
> and one acquisition path instead of nine), not the reach. That question is
> deliberately left open here; it needs its own grill and its own ADR.

**What the mapping found.** Real defects at six levels, and every one of them
was addressable without moving a boundary:

| Defect | Level | Outcome |
|---|---|---|
| One enabled model, no fallback, a human retry procedure | processor | ADR-0029 |
| Tool results as prose; the world's errors fatal | kernel loop | ADR-0030 |
| Tokens in one window of four; type and space untokenised | toolkit | ADR-0031 |
| Process logs truncated on every launch; 37 hand-made copies | observability | ADR-0032 |
| The user's reach into Felix grown by accretion | user | R7 amendment |
| Nine capability-acquisition paths, no arbitration rule | package manager | **R3** |

Five became single-ADR fixes, each contained inside one layer. The sixth --
the half of the fork that named "one acquisition path instead of nine" --
dissolved into a rule rather than a build: R3's table answers "which mechanism?"
in one question, and the nine paths stay nine because they are genuinely
different things, not nine implementations of one thing. An arbitration gap
looked like a structural gap until it was named.

**Both halves of the fork's premise were wrong.** The nine paths needed a rule,
not a merge. And "a scheduler" was the wrong noun for the other half: the R5
amendment below establishes that the contended resource is one remote endpoint,
so what is missing is admission control, not scheduling. Neither correction
required a boundary to move -- which is what a rewrite is for.

**What remains undecided.** Three things, none of them rewrite triggers:

1. **Admission control.** A max-in-flight semaphore against the one remote
   endpoint -- not a scheduler, not a queue, not preemption (see the R5
   amendment, and ADR-0029's rejection of a work queue). Nothing today bounds
   concurrent calls to a host whose known failure mode is stalling under load.
2. **Init / supervisor.** The layer that starts, restarts, and health-checks
   Cerebral (`scripts/launch-felix.ps1`, `tray/main.js`'s relaunch path,
   `tray/lib/boot-check.js`'s rollback). It is the most fragile layer in the
   system and has no ADR. Grilled separately.
3. **Categories the mapping never reached.** Testing and eval, packaging and
   updates, configuration, identity. Un-grilled, so their state is unknown
   rather than known-good.

**Revisit trigger.** A defect that cannot be fixed inside one level -- one whose
fix requires a boundary from the mapping to move. None of the six was.

## Amendment (2026-09-05) -- R7, the invocation ladder (menus, palettes, shortcuts)

R1 orders how **Felix** reaches the world. Its mirror was unwritten: how the
**user** reaches Felix. That surface has grown the same way capability did --
by accretion, with no rule.

What exists today, all hand-edited, none plugin-contributed:

| Surface | Where | State |
|---|---|---|
| Speak / type | wake word, Main window | the default, unlimited |
| Slash command | `planner.py` `_SLASH_SKILL_RE` | free per Skill, no registry |
| Federated search | `tray/lib/search-registry.js`, 14 providers | **navigates only** -- hits carry `route`+`anchor`, never an action |
| Sidebar nav | `sidebar-router.js` | hardcoded routes, "grown by campaigns" |
| Tray menu | `tray/main.js:819-871` | hardcoded `template.push` |
| App menu bar | `tray/main.js:1170` | two items |
| Global hotkey | `tray/main.js:145,173` | exactly two: PTT, video-batch toggle |
| Recipe | saved chain | the one user-authored shortcut |

**R7. The invocation ladder. A capability earns the cheapest surface that
reaches the user, and climbs only on repeat.**

> speak/type -> slash command -> command palette -> sidebar route -> menu item
> -> global hotkey

The ordering is by *scarcity*, not by effort. Speech and typing are unlimited
and cost nothing per capability. Slash commands are near-unlimited. Palette
entries are cheap and self-describing. Sidebar routes and menu items are a
small fixed budget competing for attention. Global hotkeys are the scarcest
resource in the system -- perhaps five to eight usable chords before OS and
app collisions -- and they are spent permanently.

Two tests decide a promotion:

1. **The repeat test (R2 applied to invocation).** Nothing gets a menu item,
   route, or hotkey on the first ask. Speak it; if the same invocation recurs,
   it earns a palette entry; on continued use it earns a slot. A surface added
   before the repeat is clutter that is never removed.
2. **The focus test, for hotkeys only.** A global hotkey's *only* unique
   property is firing while another application has focus. If the user is
   already in Felix, the palette is strictly better -- discoverable, unlimited,
   collision-free. So a hotkey request is granted only when the action must fire
   from inside another app. Push-to-talk and the video-batch toggle pass. Almost
   nothing else will.

**Corollary -- one build, not a menu framework.** The ladder's only genuinely
missing rung is the palette, and it is nearly built: `search-registry.js`
already ranks federated providers, but every hit navigates. Giving a hit an
optional executable action, plus one provider over tools and Recipes, turns the
existing search box into a command palette across all ~70 plugins. That is the
single thing worth building here.

Everything above it on the ladder stays hand-edited and rare **by design**. A
declarative menu-contribution API for plugins is explicitly *not* built: it
would let 70 plugins (some LLM-authored at runtime) compete for the scarcest
surfaces in the product, which is precisely the outcome R7 exists to prevent.
Plugins contribute panels (ADR-0012) and tools; they do not contribute menu
items or hotkeys.

## Amendment (2026-09-05) -- R5 restated: the scarce resource, not the GPU

R5 was written as *"One GPU is the scheduler."* Measured against the live
configuration (ADR-0029), that is wrong. Every LLM call leaves the machine:
`custom/budd-quick` is the only enabled model, every local model is disabled, and
the 1080 holds only Kokoro TTS, faster-whisper STT, and the `video` task pin. The
GPU is nearly idle; **one remote endpoint** is what everything contends for.

**R5, restated: the scarce resource is the scheduler, and it is always singular.**
Which resource it is depends on where the active model runs -- the GPU when local,
the endpoint when remote -- but there is exactly one, nothing preempts, and no
design may assume concurrency. Where two things compete, the live conversational
turn wins.

The original reasoning survives the correction: parallel fan-out is still not the
answer, it is just that the reason changed. Two local sub-agents serialise on one
card; N remote sub-agents pile onto one host whose failure mode is already
stalling under load. ADR-0020 decision 5 ("never parallel local") reached the
right conclusion from a premise that no longer describes this box.

A vocabulary correction goes with it, since it cost a full round of the grill:
a **local** model runs on the 1080, a **remote** model is reached over HTTP, and a
**cloud** model is a paid third-party API. Budd is remote and not cloud -- which is
how "I'm not using the cloud" and "everything is remote" are both true at once.
