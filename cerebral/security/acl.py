"""
Per-profile ACL resolver (Issue #45, ADR-0005).

The capability gate (`security.gate.CapabilityGate`) is a pure lookup of the
system day-1 policy. The ACL layers on top: it consults per-profile per-tool
overrides, per-profile per-class persistent grants, RAM-only session and once
grants, and finally the profile's frozen default-policy snapshot.

Resolution order on a call (issue body, ADR-0005):
  1. Per-tool override for (profile, tool)             — persistent (SQLite)
  2. Persistent class grant for (profile, class)       — persistent (SQLite)
  3. Session class grant for profile                   — RAM
  4. Once grant for (profile, class) — consume + use   — RAM
  5. Class default policy — the profile's snapshot     — taken at creation

`passive=True` continues to escalate one notch *after* ACL resolution so that
session/persistent bypasses cannot silently actuate queue-originated calls
(important — closes the ambient-actuation attack class).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from cerebral.db.profiles import ProfileManager
from cerebral.security.gate import (
    CallFlags,
    Capability,
    DEFAULT_POLICY,
    Decision,
)


# One-notch escalation for `passive` calls — identical to gate._ESCALATE, but
# copied here so the ACL is independent of the gate's private internals.
_ESCALATE: Mapping[Decision, Decision] = MappingProxyType({
    Decision.SILENT: Decision.ASK,
    Decision.ASK: Decision.DENY,
    Decision.DENY: Decision.DENY,
})


def _decision_from_str(value: str) -> Decision:
    """Map a stored policy string to a Decision. Raises ValueError on unknown."""
    return Decision(value)


class ProfileACL:
    """Resolves capability decisions for a single active profile.

    Persistent state lives in `ProfileManager`'s `profile_acl` table; once
    and session grants live on this instance and are cleared by
    `clear_transient()` (called on profile switch or Cerebral restart).
    """

    def __init__(
        self,
        profile_id: int,
        profile_manager: ProfileManager,
        defaults_snapshot: Mapping[str, str] | None = None,
    ) -> None:
        self._profile_id = profile_id
        self._pm = profile_manager
        # Frozen view of the profile's default policy at creation time.
        # If the caller passes None, fall back to the live DEFAULT_POLICY so
        # legacy profiles (no snapshot column populated) still resolve.
        if defaults_snapshot:
            self._defaults: dict[str, Decision] = {
                k: _decision_from_str(v) for k, v in defaults_snapshot.items()
            }
        else:
            self._defaults = {
                cap.value: dec for cap, dec in DEFAULT_POLICY.items()
            }
        # RAM-only grants.
        self._session_class_grants: dict[str, Decision] = {}
        # once[class_] is a queue of grants to be consumed on the next call.
        self._once_class_grants: dict[str, list[Decision]] = {}

    @property
    def profile_id(self) -> int:
        return self._profile_id

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        capability: Capability,
        tool_name: str,
        flags: CallFlags | None = None,
    ) -> Decision:
        """Resolve the effective Decision for a call.

        Returns SILENT / ASK / DENY. The orchestrator interprets these:
        SILENT → dispatch, ASK → consent surface (fail-closed to DENY in
        this slice — #48 wires the surface), DENY → block.
        """
        if not isinstance(capability, Capability):
            raise TypeError(
                f"capability must be a Capability enum value, got {type(capability).__name__}"
            )
        flags = flags or CallFlags()
        decision = self._resolve_pre_escalation(capability, tool_name)
        # ADR-0005: passive escalates AFTER ACL resolution to defeat
        # session/persistent bypasses for queue-originated calls.
        if flags.passive:
            decision = _ESCALATE[decision]
        return decision

    def _resolve_pre_escalation(
        self, capability: Capability, tool_name: str,
    ) -> Decision:
        class_target = capability.value

        # Step 1 — per-tool override (most specific).
        tool_policy = self._pm.get_acl_grant(
            self._profile_id, scope="tool", target=tool_name,
        )
        if tool_policy is not None:
            return _decision_from_str(tool_policy)

        # Step 2 — persistent class grant.
        class_policy = self._pm.get_acl_grant(
            self._profile_id, scope="class", target=class_target,
        )
        if class_policy is not None:
            return _decision_from_str(class_policy)

        # Step 3 — session class grant (RAM).
        if class_target in self._session_class_grants:
            return self._session_class_grants[class_target]

        # Step 4 — once class grant (RAM, consumed).
        once_queue = self._once_class_grants.get(class_target)
        if once_queue:
            decision = once_queue.pop(0)
            if not once_queue:
                del self._once_class_grants[class_target]
            return decision

        # Step 5 — profile's frozen default-policy snapshot.
        return self._defaults.get(class_target, DEFAULT_POLICY[capability])

    # ------------------------------------------------------------------
    # RAM-only grants (once / session) — created by the consent surface (#48).
    # ------------------------------------------------------------------

    def grant_once(self, capability: Capability, decision: Decision) -> None:
        """Stash a one-shot grant for the next call of this class."""
        self._once_class_grants.setdefault(capability.value, []).append(decision)

    def grant_session(self, capability: Capability, decision: Decision) -> None:
        """Stash a session-scoped grant for this class."""
        self._session_class_grants[capability.value] = decision

    def revoke_session(self, capability: Capability) -> bool:
        """Drop the session-scoped grant for this class, if any.

        Returns True iff a grant existed.
        """
        return self._session_class_grants.pop(capability.value, None) is not None

    def clear_transient(self) -> None:
        """Clear all once and session grants. Called on profile switch and
        on Cerebral restart (the latter is automatic because the ACL
        instance is rebuilt — no persistence)."""
        self._session_class_grants.clear()
        self._once_class_grants.clear()

    # ------------------------------------------------------------------
    # Persistent grant convenience wrappers (UI lands in #53)
    # ------------------------------------------------------------------

    def set_persistent_class(self, capability: Capability, decision: Decision) -> None:
        self._pm.set_acl_grant(
            self._profile_id, scope="class", target=capability.value,
            policy=decision.value,
        )

    def revoke_persistent_class(self, capability: Capability) -> bool:
        return self._pm.revoke_acl_grant(
            self._profile_id, scope="class", target=capability.value,
        )

    def set_tool_override(self, tool_name: str, decision: Decision) -> None:
        self._pm.set_acl_grant(
            self._profile_id, scope="tool", target=tool_name,
            policy=decision.value,
        )

    def revoke_tool_override(self, tool_name: str) -> bool:
        return self._pm.revoke_acl_grant(
            self._profile_id, scope="tool", target=tool_name,
        )

    def list_persistent_grants(self) -> list[dict]:
        """All persistent rows for this profile — used by the Permissions UI."""
        return self._pm.list_acl_grants(self._profile_id)
