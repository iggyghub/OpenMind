"""Risk heuristic for the queue admission overlay (Issue #52, ADR-0005).

A queued candidate action is flagged as "risky" when its title contains one
of a small closed verb vocabulary. The flag drives the 🛑 badge in the tray
queue list and the collapsed-by-default row treatment — it is **not** a
gate. Approval still routes through the normal capability gate / ACL /
consent surface, with the passive escalation that defeats persistent
SILENT grants for queue-originated calls.

The vocabulary is intentionally tiny: enough to surface the obvious
ambient-actuation hazards without requiring users to learn a verb-grading
DSL. Token matching is simple — no stemming. False negatives ("sent",
"sending") are accepted as the price of a predictable rule. If they prove
costly in practice the deepening path is a lemmatiser, not a longer list.
"""

from __future__ import annotations

import re

RISKY_VERBS: frozenset[str] = frozenset({
    "send",
    "transfer",
    "wire",
    "delete",
    "purchase",
    "pay",
    "unlock",
    "disable",
})

_WORD_RE = re.compile(r"\b\w+\b")


def is_risky(action_what: str) -> bool:
    if not action_what:
        return False
    tokens = _WORD_RE.findall(action_what.lower())
    return any(token in RISKY_VERBS for token in tokens)
