# 13. The queue carries proposals: memory and recipes learn through one approve/dismiss channel

Date: 2026-07-20
Status: accepted (grill session, Felix UI Round 2)

## Context

After 260 conversation turns across 3 threads, Memory, Insights and Recipes were
all empty. Investigation showed three separate causes, none of them a UI problem:

- **Memory**: the `memory_remember` tool exists and is enabled, but the entire
  system prompt is four lines that never mention remembering anything
  (`cerebral/llm/planner.py`). ChromaDB holds 2 embeddings, both on a profile that
  is not the active one, both from June.
- **Insights**: the engine works — probed against a copy of the live database, 6
  signals minted an Insight at `PATTERN_THRESHOLD = 3`. It had no input: the only
  signal source is queue approve/dismiss, and the queue's 311 rows are all Discord
  notification dismissals from a legacy popup path that predates the current handler.
- **Recipes**: only an explicit user save creates one, which CONTEXT.md's definition
  ("a saved, named, **user-approved** chain") says is correct.

So the panes were empty because nothing fed them, not because they were broken.

## Decision

1. **The queue carries proposals, not just candidate actions.** Its entries gain a
   kind: candidate action, memory proposal, recipe proposal. One approve/dismiss
   channel, one signal source, one count badge. A separate learning-proposal surface
   was rejected as a second mechanism for the same interaction.
2. **Felix proposes memories; the user confirms.** Prompt guidance leads Felix to
   raise a memory proposal when the user states a durable fact; the write happens on
   approval. Silent auto-remember was rejected against CONTEXT.md's transparency
   promise for the Insights view. A per-turn extraction pass was rejected on cost —
   a second model call per turn on a GTX 1080.
3. **Felix proposes a Recipe after a chain repeats.** A proposal, never an auto-save,
   which keeps "user-approved" literally true in the Recipe definition.
4. **Only proposals carrying a `tool_name` are insight signals.** Notification-class
   entries are excluded. The live data proves why: the insight it actually produces is
   *"Felix often handles 'Discord DM from iggyphi' actions for you"* — noise about who
   messaged the user, not a model of the user.

## Consequences

- Approving a memory proposal now also feeds Insights, so the Insights pane starts
  filling as a side effect of Memory working. The three "empty pane" symptoms share
  one fix.
- The queue count badge will carry learning proposals alongside pending actions. If
  that proves noisy in use, badge-by-kind is the escape hatch — the kind column makes
  it a filter, not a re-architecture.
- Memory quality now depends on prompt wording, which is not covered by unit tests.
  The runnable check is that the tool is offered and called on a durable-fact turn;
  whether the *right* facts get remembered is a live-verify item.
- Empty states remain worth writing, but they are no longer the fix — they are what a
  genuinely new profile should see.
