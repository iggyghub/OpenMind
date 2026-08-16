"""Approval presets over the ADR-0005 gate (harness parity H6-S1 / #737).

dsh has ctx.permissionPresets: a named preset bundles the sandbox-mode +
approval-policy knobs into one switch. OpenMind's gate has per-class grants
but no named preset. A preset here is a convenience projection ONLY -- it
writes ordinary persistent class grants via ProfileACL.set_persistent_class,
never bypasses or weakens the gate, and irreversible-action escalation (in
cerebral/mcp/orchestrator.py, untouched by this slice) still fires
regardless of preset.
"""

from __future__ import annotations

from cerebral.security.acl import ProfileACL
from cerebral.security.gate import Capability, Decision

# Every capability CLASS-level grant "full-auto" sets to SILENT, including
# shell_exec/vault_unlock/secrets_read (keep-bypass-mode-available preference:
# full-auto stays first-class, never neutered). This does NOT bypass ADR-0016
# irreversible-action escalation -- that check runs in the orchestrator, after
# ACL resolution, on every call regardless of preset.
_FULL_AUTO: dict[Capability, Decision] = {cap: Decision.SILENT for cap in Capability}

_ASK_EVERYTHING: dict[Capability, Decision] = {cap: Decision.ASK for cap in Capability}

_TRUSTED_WORKSPACE: dict[Capability, Decision] = {cap: Decision.SILENT for cap in Capability}
for _cap in (Capability.VAULT_UNLOCK, Capability.SECRETS_READ, Capability.SHELL_EXEC):
    _TRUSTED_WORKSPACE[_cap] = Decision.ASK

PRESETS: dict[str, dict[Capability, Decision]] = {
    "ask-everything": _ASK_EVERYTHING,
    "trusted-workspace": _TRUSTED_WORKSPACE,
    "full-auto": _FULL_AUTO,
}


def list_presets() -> list[str]:
    return sorted(PRESETS.keys())


def apply_preset(acl: ProfileACL, preset_name: str) -> None:
    """Write every capability's persistent class grant for the named preset.
    One preset switch = one grant-write per class. Raises ValueError on an
    unknown preset name."""
    if preset_name not in PRESETS:
        raise ValueError(f"unknown preset: {preset_name!r}")
    for capability, decision in PRESETS[preset_name].items():
        acl.set_persistent_class(capability, decision)
