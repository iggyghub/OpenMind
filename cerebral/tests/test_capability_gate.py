"""
Capability gate tests — Issue #43, ADR-0005.

Covers every class default, both flag states, the passive-escalation rule,
and the closed-vocabulary guarantee.
"""

import pytest

from cerebral.security import (
    Capability,
    CallFlags,
    CapabilityGate,
    DEFAULT_POLICY,
    Decision,
)


# ---------------------------------------------------------------------------
# Slice 1 — closed vocabulary: exactly 16 classes, no additions
# ---------------------------------------------------------------------------

EXPECTED_CLASSES = {
    "vault_unlock", "secrets_read", "fs_read", "fs_write", "fs_delete",
    "clipboard", "shell_exec", "code_install", "network_egress_local",
    "network_egress_cloud", "network_recon", "network_config",
    "external_data_read", "external_data_write", "device_control",
    "screen_capture",
}


def test_capability_enum_has_exactly_sixteen_classes():
    assert {c.value for c in Capability} == EXPECTED_CLASSES


def test_capability_enum_count_is_sixteen():
    assert len(list(Capability)) == 16


def test_capability_rejects_unknown_string():
    with pytest.raises(ValueError):
        Capability("not_a_real_class")


def test_default_policy_covers_every_class():
    assert set(DEFAULT_POLICY) == set(Capability)


def test_default_policy_is_immutable():
    # MappingProxyType cannot be mutated.
    with pytest.raises(TypeError):
        DEFAULT_POLICY[Capability.FS_READ] = Decision.DENY  # type: ignore[index]


# ---------------------------------------------------------------------------
# Slice 2 — day-1 class defaults (passive=False, irreversible=False)
# ---------------------------------------------------------------------------

SILENT_CLASSES = [
    Capability.FS_READ,
    Capability.CLIPBOARD,
    Capability.NETWORK_EGRESS_LOCAL,
    Capability.NETWORK_EGRESS_CLOUD,
    Capability.EXTERNAL_DATA_READ,
    Capability.DEVICE_CONTROL,
]

ASK_CLASSES = [
    Capability.VAULT_UNLOCK,
    Capability.SECRETS_READ,
    Capability.FS_WRITE,
    Capability.FS_DELETE,
    Capability.CODE_INSTALL,
    Capability.NETWORK_RECON,
    Capability.NETWORK_CONFIG,
    Capability.EXTERNAL_DATA_WRITE,
    Capability.SCREEN_CAPTURE,
]

DENY_CLASSES = [Capability.SHELL_EXEC]


@pytest.mark.parametrize("capability", SILENT_CLASSES)
def test_silent_class_default(capability):
    assert CapabilityGate().check(capability) is Decision.SILENT


@pytest.mark.parametrize("capability", ASK_CLASSES)
def test_ask_class_default(capability):
    assert CapabilityGate().check(capability) is Decision.ASK


@pytest.mark.parametrize("capability", DENY_CLASSES)
def test_deny_class_default(capability):
    assert CapabilityGate().check(capability) is Decision.DENY


def test_default_class_split_matches_adr():
    # Exhaustive: every class falls into exactly one bucket.
    assert set(SILENT_CLASSES) | set(ASK_CLASSES) | set(DENY_CLASSES) == set(Capability)
    assert len(SILENT_CLASSES) + len(ASK_CLASSES) + len(DENY_CLASSES) == 16


# ---------------------------------------------------------------------------
# Slice 3 — passive=True escalates one notch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("capability", SILENT_CLASSES)
def test_passive_escalates_silent_to_ask(capability):
    decision = CapabilityGate().check(capability, CallFlags(passive=True))
    assert decision is Decision.ASK


@pytest.mark.parametrize("capability", ASK_CLASSES)
def test_passive_escalates_ask_to_deny(capability):
    decision = CapabilityGate().check(capability, CallFlags(passive=True))
    assert decision is Decision.DENY


@pytest.mark.parametrize("capability", DENY_CLASSES)
def test_passive_does_not_change_deny(capability):
    decision = CapabilityGate().check(capability, CallFlags(passive=True))
    assert decision is Decision.DENY


def test_passive_false_matches_no_flags():
    gate = CapabilityGate()
    for capability in Capability:
        assert gate.check(capability) == gate.check(capability, CallFlags(passive=False))


# ---------------------------------------------------------------------------
# Slice 4 — irreversible is representable but has no behaviour in this slice
# ---------------------------------------------------------------------------

def test_irreversible_flag_is_representable():
    flags = CallFlags(irreversible=True)
    assert flags.irreversible is True
    assert flags.passive is False


def test_irreversible_does_not_alter_decision_in_this_slice():
    # The modal that consumes `irreversible` lands in #49. The gate must not
    # yet treat it as a policy modifier.
    gate = CapabilityGate()
    for capability in Capability:
        baseline = gate.check(capability)
        with_flag = gate.check(capability, CallFlags(irreversible=True))
        assert with_flag is baseline, f"irreversible changed decision for {capability!r}"


def test_irreversible_and_passive_together_escalate_per_passive():
    # `irreversible` is inert in this slice; `passive` still escalates.
    gate = CapabilityGate()
    flags = CallFlags(passive=True, irreversible=True)
    assert gate.check(Capability.FS_READ, flags) is Decision.ASK
    assert gate.check(Capability.FS_WRITE, flags) is Decision.DENY
    assert gate.check(Capability.SHELL_EXEC, flags) is Decision.DENY


# ---------------------------------------------------------------------------
# Slice 5 — call-flag defaults and frozenness
# ---------------------------------------------------------------------------

def test_call_flags_default_to_false():
    flags = CallFlags()
    assert flags.passive is False
    assert flags.irreversible is False


def test_call_flags_are_frozen():
    flags = CallFlags()
    with pytest.raises(Exception):
        flags.passive = True  # type: ignore[misc]


def test_gate_check_with_none_flags_matches_default_flags():
    gate = CapabilityGate()
    for capability in Capability:
        assert gate.check(capability, None) == gate.check(capability, CallFlags())


# ---------------------------------------------------------------------------
# Slice 6 — closed-vocabulary enforcement at the gate
# ---------------------------------------------------------------------------

def test_gate_rejects_non_capability_arg():
    with pytest.raises(TypeError):
        CapabilityGate().check("fs_read")  # type: ignore[arg-type]


def test_gate_rejects_partial_policy():
    incomplete = {Capability.FS_READ: Decision.SILENT}
    with pytest.raises(ValueError):
        CapabilityGate(policy=incomplete)


def test_gate_accepts_custom_full_policy():
    # An injected policy must cover every class.
    custom = {c: Decision.DENY for c in Capability}
    gate = CapabilityGate(policy=custom)
    for capability in Capability:
        assert gate.check(capability) is Decision.DENY
