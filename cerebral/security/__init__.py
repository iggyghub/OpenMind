"""
Capability vocabulary and the orchestrator-side permission gate (ADR-0005).

The 16-class enum is the closed vocabulary every plugin declares against and
every call site checks. The gate looks up the day-1 default policy, applies
the `passive` escalation, and returns a SILENT / ASK / DENY decision. The
orchestrator decides what to do with each (ASK resolves to DENY in this slice;
the consent surface lands in #48).
"""

from cerebral.security.acl import ProfileACL
from cerebral.security.call_site_capabilities import (
    CompletenessError,
    Finding,
    REASON_UNDER_DECLARED,
    assert_complete,
    check_completeness,
    format_findings,
)
from cerebral.security.consent import (
    CHOICE_DENY,
    CHOICE_ONCE,
    CHOICE_PERSISTENT,
    CHOICE_SESSION,
    ConsentRequest,
    ConsentSurface,
    build_args_preview,
    is_valid_choice,
)
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
from cerebral.security.labels import (
    CAPABILITY_DESCRIPTION,
    CAPABILITY_LABEL,
    description_for,
    label_for,
)
from cerebral.security.modal import (
    CHOICE_ACCEPT,
    CHOICE_CANCEL,
    ModalRequest,
    ModalSurface,
    is_valid_modal_choice,
)

# Closed string-form view of the 16-class vocabulary. Plugin modules declare
# REQUIRED_CAPABILITIES as frozenset[str] so they don't have to import the
# Capability enum; the orchestrator validates declarations against this set
# at registration time (Issue #44 / ADR-0005).
CAPABILITY_VOCABULARY: frozenset[str] = frozenset(c.value for c in Capability)

__all__ = [
    "CAPABILITY_DESCRIPTION",
    "CAPABILITY_LABEL",
    "CAPABILITY_VOCABULARY",
    "CHOICE_ACCEPT",
    "CHOICE_CANCEL",
    "CHOICE_DENY",
    "CHOICE_ONCE",
    "CHOICE_PERSISTENT",
    "CHOICE_SESSION",
    "Capability",
    "CallFlags",
    "CapabilityGate",
    "CompletenessError",
    "ConsentRequest",
    "ConsentSurface",
    "DEFAULT_POLICY",
    "Decision",
    "Finding",
    "FORBIDDEN_PATTERNS",
    "INSPECTED",
    "InspectabilityIssue",
    "ModalRequest",
    "ModalSurface",
    "ProfileACL",
    "REASON_FORBIDDEN_PATTERN",
    "REASON_NON_TEXT",
    "REASON_NOT_INSPECTABLE_PATH",
    "REASON_UNDER_DECLARED",
    "TRUSTED",
    "assert_complete",
    "build_args_preview",
    "check_completeness",
    "classify_plugin_path",
    "description_for",
    "format_findings",
    "is_valid_choice",
    "is_valid_modal_choice",
    "label_for",
    "scan_source",
]
