---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving every open branch of the decision tree before any work starts. Use when the user wants their plan stress-tested, asks to be "grilled" on a design, says "grill me", or proposes a non-trivial change without having settled the details first.
kind: procedure
tools: []
---

# Grill me

Interview the user relentlessly about their plan or design until you both share
the same understanding. Walk down each branch of the decision tree, resolving
dependencies between decisions one at a time. Do not start building until the
grill is done.

## Rules

- Ask one question at a time. Do not dump a list of questions.
- For every question, give your own recommended answer -- do not just ask, propose.
- If a question can be settled by exploring the codebase or files you already
  have access to, do that first instead of asking the user.
- Resolve branches in dependency order: settle the decision that other answers
  hinge on before asking about what depends on it.
- Keep going until every open branch is resolved, not just until the user seems
  satisfied with the first few answers.

## Wrap-up

Once every branch is resolved, summarize the shared plan back to the user in a
short numbered list before treating the design as settled.
