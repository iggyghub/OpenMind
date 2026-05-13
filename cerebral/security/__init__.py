"""
Capability vocabulary and the orchestrator-side permission gate (ADR-0005).

The 16-class enum is the closed vocabulary every plugin declares against and
every call site checks. The gate looks up the day-1 default policy, applies
the `passive` escalation, and returns a SILENT / ASK / DENY decision. The
orchestrator decides what to do with each (ASK resolves to DENY in this slice;
the consent surface lands in #48).
"""

from cerebral.security.acl import ProfileACL
from cerebral.security.gate import (
    Capability,
    CallFlags,
    CapabilityGate,
    DEFAULT_POLICY,
    Decision,
)
from cerebral.security.inspectability import (
    FORBIDDEN_PATTERNS,
    INSPECTED,
    TRUSTED,
    InspectabilityIssue,
    REASON_FORBIDDEN_PATTERN,
    REASON_NON_TEXT,
    REASON_NOT_INSPECTABLE_PATH,
    classify_path as classify_plugin_path,
    scan_source,
)

# Closed string-form view of the 16-class vocabulary. Plugin modules declare
# REQUIRED_CAPABILITIES as frozenset[str] so they don't have to import the
# Capability enum; the orchestrator validates declarations against this set
# at registration time (Issue #44 / ADR-0005).
CAPABILITY_VOCABULARY: frozenset[str] = frozenset(c.value for c in Capability)

__all__ = [
    "Capability",
    "CallFlags",
    "CapabilityGate",
    "CAPABILITY_VOCABULARY",
    "DEFAULT_POLICY",
    "Decision",
    "FORBIDDEN_PATTERNS",
    "INSPECTED",
    "InspectabilityIssue",
    "ProfileACL",
    "REASON_FORBIDDEN_PATTERN",
    "REASON_NON_TEXT",
    "REASON_NOT_INSPECTABLE_PATH",
    "TRUSTED",
    "classify_plugin_path",
    "scan_source",
]
