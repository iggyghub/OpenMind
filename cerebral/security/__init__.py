"""
Capability vocabulary and the orchestrator-side permission gate (ADR-0005).

The 16-class enum is the closed vocabulary every plugin declares against and
every call site checks. The gate looks up the day-1 default policy, applies
the `passive` escalation, and returns a SILENT / ASK / DENY decision. The
orchestrator decides what to do with each (ASK resolves to DENY in this slice;
the consent surface lands in #48).
"""

from cerebral.security.gate import (
    Capability,
    CallFlags,
    CapabilityGate,
    DEFAULT_POLICY,
    Decision,
)

__all__ = [
    "Capability",
    "CallFlags",
    "CapabilityGate",
    "DEFAULT_POLICY",
    "Decision",
]
