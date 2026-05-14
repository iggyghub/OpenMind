"""
Irreversible-flag modal surface — Issue #49, ADR-0005.

When the orchestrator's call_tool ladder encounters ``CallFlags.irreversible
=True`` AND the gate/ACL did not already DENY, the call routes through
*this* surface — not the consent surface (#48). The modal is intentionally
narrower:

  - Two buttons, Accept / Cancel. No "session" or "persistent" option:
    acceptance is one-shot, never persisted (ADR-0005 / AC#4).
  - The surface carries no ACL — there is nothing to mutate. Defence-in-
    depth: the type system makes it impossible to "remember" an
    irreversible grant by accident.
  - Fires even when a Session/Persistent grant covers the class (AC#2).
    The orchestrator routes irreversible to the modal *before* the
    consent path, so the grant's silent dispatch never applies.

Fail-closed rules (ADR-0005, shared with the consent surface):
  1. No subscriber to the modal channel → DENY without emitting a request.
  2. Timeout (``OPENMIND_CONSENT_TIMEOUT_SEC`` — single shared knob in v1;
     a separate ``OPENMIND_MODAL_TIMEOUT_SEC`` can land later if a
     longer modal-only window proves useful) → DENY, no dispatch.
  3. Unknown choice from the tray → DENY rather than risk an upgrade.

The visualiser is a 200x200 transparent click-through window, not a
viable modal host; the tray instead opens a dedicated BrowserWindow
``tray/windows/irreversible-modal.html`` anchored to the visualiser UX
but standalone in practice. See the issue #49 sharpener for the
documented deviation from ADR-0005's "modal in the visualiser window"
wording.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

import asyncio

from cerebral.security.consent import _timeout_seconds, build_args_preview
from cerebral.security.gate import CallFlags, Capability, Decision
from cerebral.security.labels import description_for, label_for

logger = logging.getLogger(__name__)


# Stable choice strings carried over IPC. Distinct from the consent
# surface's four — the tray's modal manager rejects "once" / "session" /
# "persistent" / "deny" since they have no meaning here.
CHOICE_ACCEPT = "accept"
CHOICE_CANCEL = "cancel"
_VALID_CHOICES = frozenset({CHOICE_ACCEPT, CHOICE_CANCEL})


@dataclass(frozen=True)
class ModalRequest:
    """Tray-bound payload for an irreversible-flag confirmation prompt.

    The IPC envelope (``type``, ``data``) matches the shape every other
    Cerebral → tray event uses, so the tray's dispatcher can route on
    ``event.type`` and read fields off ``event.data`` uniformly.
    """

    request_id: str
    tool_name: str
    capability: Capability
    flags: CallFlags
    args_preview: dict[str, object]

    def to_ipc(self) -> dict:
        return {
            "type": "irreversible_modal_request",
            "data": {
                "request_id": self.request_id,
                "tool_name": self.tool_name,
                "capability": self.capability.value,
                "capability_label": label_for(self.capability),
                "capability_description": description_for(self.capability),
                "args_preview": dict(self.args_preview),
                "flags": {
                    "passive": self.flags.passive,
                    "irreversible": self.flags.irreversible,
                },
            },
        }


# Function the surface calls to push a request to the tray. The returned
# coroutine resolves to one of ``CHOICE_ACCEPT`` / ``CHOICE_CANCEL``.
# Implementations route over WebSocket; tests inject a fake.
ModalPromptFn = Callable[[ModalRequest], Awaitable[str]]

# Function reporting whether a tray client is currently subscribed. When
# False, the surface fail-closes to DENY without emitting a request — the
# ADR-0005 "no surface" rule, important here because irreversible calls
# are precisely the ones where a silent grant from a missing UI would
# be most damaging.
HasSubscriberFn = Callable[[], bool]


class ModalSurface:
    """Bridges orchestrator irreversible-flag routing to a tray modal.

    Wired up in ``cerebral.main`` against the WebSocket IPC; tests build
    one with an injected ``prompt_fn`` and ``has_subscriber_fn``. The
    surface intentionally does NOT carry an ACL — irreversible
    acceptance is one-shot, never persisted (ADR-0005 / AC#4).
    """

    def __init__(
        self,
        prompt_fn: ModalPromptFn,
        *,
        has_subscriber_fn: HasSubscriberFn | None = None,
        request_id_fn: Callable[[], str] | None = None,
    ) -> None:
        self._prompt = prompt_fn
        self._has_subscriber = has_subscriber_fn or (lambda: True)
        self._request_id_fn = request_id_fn or (lambda: str(uuid.uuid4()))

    async def request(
        self,
        capability: Capability,
        tool_name: str,
        args: Mapping[str, object] | None,
        flags: CallFlags | None = None,
    ) -> Decision:
        """Ask the user. Returns SILENT on Accept, DENY on Cancel /
        timeout / no-surface / unknown-choice."""
        flags = flags or CallFlags()

        if not self._has_subscriber():
            logger.info(
                "[modal] No tray subscriber for irreversible '%s' "
                "(capability=%s) — fail-closed DENY",
                tool_name, capability.value,
            )
            return Decision.DENY

        req = ModalRequest(
            request_id=self._request_id_fn(),
            tool_name=tool_name,
            capability=capability,
            flags=flags,
            args_preview=build_args_preview(args),
        )

        try:
            choice = await asyncio.wait_for(
                self._prompt(req), timeout=_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            logger.info(
                "[modal] Timeout waiting on irreversible '%s' "
                "(capability=%s) — DENY",
                tool_name, capability.value,
            )
            return Decision.DENY

        if choice == CHOICE_ACCEPT:
            return Decision.SILENT
        if choice == CHOICE_CANCEL:
            return Decision.DENY
        logger.warning(
            "[modal] Unknown choice %r for request %s — DENY",
            choice, req.request_id,
        )
        return Decision.DENY


def is_valid_modal_choice(choice: object) -> bool:
    """True iff a tray-emitted choice string is Accept or Cancel."""
    return isinstance(choice, str) and choice in _VALID_CHOICES
