"""
Capability vocabulary, call flags, and the policy gate — Issue #43, ADR-0005.

The 16-class vocabulary is closed: `Capability` is an Enum, attempts to use a
value outside it are a programming error. The day-1 policy maps each class to
SILENT / ASK / DENY. `CapabilityGate.check(capability, flags)` returns the
class default escalated for the `passive` flag.

What this slice does NOT do (lands in later issues):
  - `irreversible` is representable but has no behaviour here (#49 wires the
    modal).
  - Per-profile ACL overrides (#45).
  - Runtime ASK→user-prompt resolution (#48). In #43 the orchestrator treats
    ASK as DENY (fail-closed) because there is no surface to ask through yet.
"""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class Capability(str, Enum):
    """The 16 closed capability classes. No additions outside an ADR."""

    VAULT_UNLOCK = "vault_unlock"
    SECRETS_READ = "secrets_read"
    FS_READ = "fs_read"
    FS_WRITE = "fs_write"
    FS_DELETE = "fs_delete"
    CLIPBOARD = "clipboard"
    SHELL_EXEC = "shell_exec"
    CODE_INSTALL = "code_install"
    NETWORK_EGRESS_LOCAL = "network_egress_local"
    NETWORK_EGRESS_CLOUD = "network_egress_cloud"
    NETWORK_RECON = "network_recon"
    NETWORK_CONFIG = "network_config"
    EXTERNAL_DATA_READ = "external_data_read"
    EXTERNAL_DATA_WRITE = "external_data_write"
    DEVICE_CONTROL = "device_control"
    SCREEN_CAPTURE = "screen_capture"


class Decision(str, Enum):
    SILENT = "silent"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class CallFlags:
    passive: bool = False
    irreversible: bool = False


# Day-1 ACL: ADR-0005 "Default policy per class".
DEFAULT_POLICY: Mapping[Capability, Decision] = MappingProxyType({
    # silent
    Capability.FS_READ: Decision.SILENT,
    Capability.CLIPBOARD: Decision.SILENT,
    Capability.NETWORK_EGRESS_LOCAL: Decision.SILENT,
    Capability.NETWORK_EGRESS_CLOUD: Decision.SILENT,
    Capability.EXTERNAL_DATA_READ: Decision.SILENT,
    Capability.DEVICE_CONTROL: Decision.SILENT,
    # ask
    Capability.VAULT_UNLOCK: Decision.ASK,
    Capability.SECRETS_READ: Decision.ASK,
    Capability.FS_WRITE: Decision.ASK,
    Capability.FS_DELETE: Decision.ASK,
    Capability.CODE_INSTALL: Decision.ASK,
    Capability.NETWORK_RECON: Decision.ASK,
    Capability.NETWORK_CONFIG: Decision.ASK,
    Capability.EXTERNAL_DATA_WRITE: Decision.ASK,
    Capability.SCREEN_CAPTURE: Decision.ASK,
    # deny
    Capability.SHELL_EXEC: Decision.DENY,
})


# One-notch escalation for `passive` calls.
_ESCALATE: Mapping[Decision, Decision] = MappingProxyType({
    Decision.SILENT: Decision.ASK,
    Decision.ASK: Decision.DENY,
    Decision.DENY: Decision.DENY,
})


class CapabilityGate:
    """The orchestrator-side gate. Pure policy lookup — no I/O, no consent surface."""

    def __init__(self, policy: Mapping[Capability, Decision] | None = None) -> None:
        source = policy if policy is not None else DEFAULT_POLICY
        missing = set(Capability) - set(source)
        if missing:
            raise ValueError(
                f"Policy is missing entries for: {sorted(c.value for c in missing)}"
            )
        self._policy: dict[Capability, Decision] = dict(source)

    def check(self, capability: Capability, flags: CallFlags | None = None) -> Decision:
        if not isinstance(capability, Capability):
            raise TypeError(
                f"capability must be a Capability enum value, got {type(capability).__name__}"
            )
        decision = self._policy[capability]
        if flags is not None and flags.passive:
            decision = _ESCALATE[decision]
        return decision
