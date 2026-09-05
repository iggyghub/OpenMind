# ADR-0029: The model is the processor -- shell mode and the boot menu

**Date:** 2026-09-05
**Status:** Accepted (grill session)
**Relates:** ADR-0028 (R5, R7), ADR-0015 (self-dev restart), ADR-0027 (visual tokens).
**Depends on:** nothing. **Blocks nothing.** Its *scope* depends on ADR-0030 -- see Open.

## Context

An OS mapping of Felix (grill, 2026-09-05) covered thirteen levels and omitted
one: **the processor.** Felix's is the model. Everything above L2 is written in
terms of a model that can select a tool, and nothing above L2 runs without one.

The live configuration makes that a single point of failure:

| Model | Where | Enabled |
|---|---|---|
| `custom/budd-quick` | `bonsai.ai-dabs.com` | **the only enabled model** |
| `custom/budd-code` | `bonsai.ai-dabs.com` | no |
| `claude/sonnet`, `claude/haiku` | Anthropic via OpenClaw | no |
| `ollama/qwen2.5-coder:7b`, `ollama/qwen3:8b` | local 1080 | no (qwen3:8b pinned for `video`) |

`_fallback_enabled` is off, and nothing is enabled beneath Budd to fall through
to. When it stalls -- a known, recurring condition -- `ModelRouter.complete`
raises `all enabled models unavailable` (`router.py:572`) and the turn's work is
discarded. The documented response is a *human* procedure: wait five minutes,
retry, up to five times. That is a person standing in for a system behaviour
that does not exist.

Two corrections this forced on ADR-0028:

- **R5 ("one GPU is the scheduler") is mis-stated for this configuration.** Every
  LLM call leaves the machine. The 1080 holds Kokoro TTS, faster-whisper STT, and
  the `video` task pin -- nothing else. The scarce resource is **one endpoint**,
  not one GPU.
- **"Cloud" is two things.** A *local* model runs on the 1080; a *remote* model is
  reached over HTTP; a *cloud* model is a paid third-party API. Budd is remote and
  not cloud, which is why "I'm not using the cloud" and "everything is remote" are
  both true.

## Decision

1. **Felix gets a degraded boot, not a halt and not a work queue.** A queue that
   honours work after an outage was considered and rejected for now: it needs a
   job table, cross-process IPC, and a UI, and it defers rather than answers.

2. **The degraded mode runs a weaker model, not no model.** A no-model command
   shell (exact tool invocation, no planner) was the alternative and is strictly
   more robust -- it has no capability floor, so it can never itself be down.
   Rejected deliberately: the invocation ladder (ADR-0028 R7) already degrades
   that way on its own, and a shell that cannot plan is not the Felix wanted
   during an outage.

3. **The shell model is user-chosen, as the `shell` task pin.** It is picked in
   the AI selector like any other per-task pin. This reuses the whole existing
   path -- the `model_task_pin` table, the `set_task_model` IPC, the picker
   rendered from `model-menu.js`'s `taskTypes` array -- and inherits the property
   that matters: **a task pin resolves before the enabled check**
   (`router.py:532`), so the shell model routes while sitting disabled in the
   priority list. `qwen3:8b` already proves this for `video`.

4. **The selector warns when the shell model shares a failure domain with the
   primary.** Both Budd entries live on one host; choosing one as the other's
   fallback is a fallback that dies with what it is backing. The check is a
   hostname comparison against the stored `url`, and it *warns* rather than
   blocks -- a genuinely separate second provider is a legitimate future config,
   and local-vs-remote is the wrong axis for the test.

5. **Shell mode is entered from a boot menu, and is ephemeral.** It governs
   exactly one boot and never writes to the priority store. A diagnostic mode
   that silently persists is how a week gets spent debugging the wrong Felix.

6. **The boot menu occupies the cold start, and takes focus only on a cold
   start.** Cerebral needs 30-45s to accept connections on `:7766`
   (`boot-check.js:8`); the tray already waits out that silence. The menu is
   shown on **every** start as boot feedback, but accepts the Space+Enter chord
   only when the launch was user-initiated -- never on a self-dev restart, a
   boot-check rollback, or Restart Felix. Those are frequent and unattended, and
   a splash that grabbed focus on each would eat keystrokes out of whatever the
   user is actually typing in.

7. **It lives in the Electron layer, not Cerebral.** The same reason `boot-check.js`
   does, in its own words: a broken brain cannot rescue itself.

## Consequences

- A Budd outage stops being a Felix outage. It becomes a slower Felix, chosen
  deliberately at boot.
- The 30-45s cold start stops being silent -- currently there is no indication
  Felix is starting at all until the tray goes live.
- The chord is **window-scoped**, so it spends nothing from ADR-0028 R7's global
  hotkey budget.
- One new window (`tray/windows/boot.html`) joins the four that already exist,
  and inherits ADR-0031's shared tokens rather than starting its own palette.
- `_local_only` mode stops being a trap. Today, flipping it with every local
  model disabled leaves zero routable models.
- The boot menu is a **slot**, and deliberately not filled: rollback, profile
  choice, and model choice are all plausible entries and none is built. It is a
  mode selector with one mode.

## Open

**Whether decision 2 survives ADR-0030.** Some of the capability floor that makes
this ADR necessary is manufactured: the chain feeds tool results back as flat
prose rather than as tool messages, and a model reconstructing what happened from
narration needs far more capability than one reading structured results. If
ADR-0030 lowers the floor enough for `qwen3:8b` to plan reliably, shell mode
becomes genuinely useful rather than a consolation. If it lowers it further still,
some of this ADR may prove unnecessary. **Build ADR-0030 first and re-measure
before sizing the work here.**
