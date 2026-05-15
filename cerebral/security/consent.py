"""
Tray consent surface — Issue #48, ADR-0005.

The capability gate (#43) returns ASK; the per-profile ACL (#45) layers
overrides on top. When the effective decision is ASK, *this* module bridges
the orchestrator to the user via a tray notification with inline buttons
(Once / Session / Persistent / Deny).

Contract (the orchestrator's view):
    ConsentSurface.request(capability, tool_name, args, flags) -> Decision

Returns `Decision.SILENT` on Allow (Once/Session/Persistent — the ACL has
been mutated for Session/Persistent by the time the call returns) or
`Decision.DENY` on Deny / no surface / timeout.

Fail-closed rules (ADR-0005):
  1. No tray subscriber AND no voice surface → DENY without emitting a
     prompt.
  2. 30s timeout (OPENMIND_CONSENT_TIMEOUT_SEC) → DENY, no ACL mutation.

Voice surface (Issue #50):
  When ``voice_prompt_fn`` is wired (TTS + Vosk ready), the surface races
  the tray prompt against a voice prompt inside the same per-class lock.
  Whichever resolves first wins; the loser is cancelled. When only one
  surface is available (e.g. no tray subscriber but voice is up), only
  that surface is used — voice does NOT require a tray subscriber.

Irreversible-flagged calls are routed by the orchestrator to a separate
``ModalSurface`` (#49) *before* reaching this surface — see
``MCPOrchestrator.call_tool``. This surface deliberately does not
re-check ``flags.irreversible``: the orchestrator's routing rule is
the single source of truth, and a defensive double-check here would
just hide a future routing bug.

Concurrency:
  Per-(profile_id, capability) prompt serialisation. A second call of the
  same class while a prompt is open *waits* — when the user resolves, the
  waiter re-resolves through the ACL (so Once is consumed once, Session/
  Persistent satisfies the second call silently, Deny re-prompts).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from cerebral.security.acl import ProfileACL
from cerebral.security.gate import CallFlags, Capability, Decision
from cerebral.security.labels import description_for, label_for

logger = logging.getLogger(__name__)


# Default timeout — overridden by OPENMIND_CONSENT_TIMEOUT_SEC. The env
# variable is read at *every* prompt so tests can monkeypatch without
# rebuilding the surface, and so an operator can tune without restart.
DEFAULT_TIMEOUT_SECONDS = 30.0
_TIMEOUT_ENV_VAR = "OPENMIND_CONSENT_TIMEOUT_SEC"

# Stable choice strings carried over IPC. Match the sharpener's verbs.
CHOICE_ONCE = "once"
CHOICE_SESSION = "session"
CHOICE_PERSISTENT = "persistent"
CHOICE_DENY = "deny"
_VALID_CHOICES = frozenset({CHOICE_ONCE, CHOICE_SESSION, CHOICE_PERSISTENT, CHOICE_DENY})

# Max length per arg value in the preview. Long strings are truncated so the
# tray notification has a predictable rendering envelope. Per sharpener #8.
ARGS_PREVIEW_MAX_VALUE_CHARS = 200


def _timeout_seconds() -> float:
    raw = os.environ.get(_TIMEOUT_ENV_VAR)
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[consent] %s=%r is not a number — using default %.0fs",
            _TIMEOUT_ENV_VAR, raw, DEFAULT_TIMEOUT_SECONDS,
        )
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        logger.warning(
            "[consent] %s=%s must be positive — using default %.0fs",
            _TIMEOUT_ENV_VAR, value, DEFAULT_TIMEOUT_SECONDS,
        )
        return DEFAULT_TIMEOUT_SECONDS
    return value


def _truncate(value: object, limit: int = ARGS_PREVIEW_MAX_VALUE_CHARS) -> object:
    """Reduce a single arg value to something a notification can render.

    Strings over `limit` chars are truncated with an ellipsis marker.
    Non-str scalars pass through. Containers are stringified then truncated
    (mirrors the user's expectation that the preview is human-readable).
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_args_preview(args: Mapping[str, object] | None) -> dict[str, object]:
    """Per-value-truncated copy of tool args for the tray notification."""
    if not args:
        return {}
    return {str(k): _truncate(v) for k, v in args.items()}


@dataclass(frozen=True)
class ConsentRequest:
    """The payload the tray renders. Stable IPC shape (see sharpener #6)."""

    request_id: str
    tool_name: str
    capability: Capability
    flags: CallFlags
    args_preview: dict[str, object]

    def to_ipc(self) -> dict:
        """Render as a Cerebral → tray WebSocket event.

        Wraps the payload under ``data`` to match every other event the
        IPC dispatcher emits (``profile_loaded``, ``queue_update``,
        ``plugins_list``, …). The tray's ``event.data`` access is uniform.
        """
        return {
            "type": "consent_request",
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


# Type of the function the surface calls to push a request to the tray. The
# return value is a coroutine resolving to the chosen string ("once",
# "session", "persistent", "deny"). Implementations route over WebSocket;
# tests inject a fake.
PromptFn = Callable[[ConsentRequest], Awaitable[str]]


# Type of the function that reports whether the tray (or any consent
# subscriber) is connected at the moment a prompt would fire. When this
# returns False AND no voice prompt is wired the surface DENIES without
# emitting a request — that's the "no surface" fail-closed path from
# ADR-0005.
HasSubscriberFn = Callable[[], bool]


# Type of an optional voice prompt (Issue #50). Returns the same four-verb
# vocabulary (typically "once" or "deny" — voice does not offer Session
# or Persistent), so the surface's choice validation and ACL-mutation
# logic stay untouched. Implementations live in ``voice_consent.py``;
# tests inject a fake.
VoicePromptFn = Callable[[ConsentRequest], Awaitable[str]]


class ConsentSurface:
    """Bridges orchestrator ASK decisions to a tray prompt.

    Wired up in `cerebral.main` against the WebSocket IPC; tests build one
    with an injected `prompt_fn` and `has_subscriber_fn`.

    The surface is profile-scoped: it mutates the ACL it holds. When the
    profile switches, `main.py` builds a fresh ACL and a fresh surface (or
    rebinds via `set_acl`).
    """

    def __init__(
        self,
        prompt_fn: PromptFn,
        *,
        has_subscriber_fn: HasSubscriberFn | None = None,
        acl: ProfileACL | None = None,
        request_id_fn: Callable[[], str] | None = None,
        voice_prompt_fn: VoicePromptFn | None = None,
    ) -> None:
        self._prompt = prompt_fn
        self._has_subscriber = has_subscriber_fn or (lambda: True)
        self._acl = acl
        self._request_id_fn = request_id_fn or (lambda: str(uuid.uuid4()))
        # Optional voice prompt (Issue #50). When set, ``request`` races
        # the tray prompt against the voice prompt inside the per-class
        # lock and returns whichever resolves first.
        self._voice_prompt_fn = voice_prompt_fn
        # Per-(profile_id, capability) serialisation. The lock guards the
        # window between "decide to prompt" and "ACL has the new grant" so
        # a second call sees the freshly written grant.
        self._locks: dict[tuple[int | None, str], asyncio.Lock] = {}

    def set_acl(self, acl: ProfileACL | None) -> None:
        """Swap the active profile's ACL — called by main on profile switch."""
        self._acl = acl
        # Old per-class locks belong to the old profile. Drop them; any
        # awaiter on an old lock will simply finish through the old
        # profile's resolution. Fresh locks form for the new profile.
        self._locks.clear()

    def set_voice_prompt_fn(self, fn: VoicePromptFn | None) -> None:
        """Wire (or unwire) the voice surface (Issue #50).

        main.py calls this after the audio pipeline starts — voice consent
        depends on the pipeline's loaded Vosk model. Passing ``None``
        disables voice and reverts to tray-only behaviour.
        """
        self._voice_prompt_fn = fn

    @property
    def acl(self) -> ProfileACL | None:
        return self._acl

    @property
    def voice_prompt_fn(self) -> VoicePromptFn | None:
        return self._voice_prompt_fn

    async def request(
        self,
        capability: Capability,
        tool_name: str,
        args: Mapping[str, object] | None,
        flags: CallFlags | None = None,
    ) -> Decision:
        """Ask the user. Returns SILENT on allow, DENY on refuse/timeout/no-surface.

        Irreversible-flagged calls never reach this method: the
        orchestrator's ``call_tool`` ladder routes them to ``ModalSurface``
        before consulting this surface (Issue #49).
        """
        flags = flags or CallFlags()

        tray_available = self._has_subscriber()
        # Snapshot the voice fn so a concurrent set_voice_prompt_fn(None)
        # during our lock-acquisition window cannot strip us mid-flight.
        voice_fn = self._voice_prompt_fn
        voice_available = voice_fn is not None

        # Sharpener #5 (extended by #50): if BOTH surfaces are unavailable
        # we fail closed without emitting anything. Voice does NOT require
        # a tray subscriber, so a Cerebral with no tray client but a
        # working mic+speakers still prompts via voice.
        if not tray_available and not voice_available:
            logger.info(
                "[consent] No consent surface available for '%s' (capability=%s) — "
                "fail-closed DENY", tool_name, capability.value,
            )
            return Decision.DENY

        profile_id = self._acl.profile_id if self._acl is not None else None
        lock = self._locks.setdefault((profile_id, capability.value), asyncio.Lock())

        async with lock:
            # Sharpener #4: while we were waiting on the lock, an earlier
            # caller may have written a Session/Persistent grant that
            # satisfies us. Re-resolve through the ACL; only call the
            # tray if it still says ASK.
            if self._acl is not None:
                resolved = self._acl.resolve(capability, tool_name, flags)
                if resolved is Decision.SILENT:
                    return Decision.SILENT
                if resolved is Decision.DENY:
                    return Decision.DENY
                # else: still ASK — prompt.

            req = ConsentRequest(
                request_id=self._request_id_fn(),
                tool_name=tool_name,
                capability=capability,
                flags=flags,
                args_preview=build_args_preview(args),
            )

            choice = await self._collect_choice(req, tray_available, voice_fn)
            if choice not in _VALID_CHOICES:
                logger.warning(
                    "[consent] Unknown choice %r for request %s — DENY",
                    choice, req.request_id,
                )
                return Decision.DENY

            return self._apply_choice(capability, choice)

    async def _collect_choice(
        self,
        req: ConsentRequest,
        tray_available: bool,
        voice_fn: VoicePromptFn | None,
    ) -> str:
        """Run the tray prompt and (optionally) the voice prompt in parallel.

        Returns the first valid choice string emitted, or ``CHOICE_DENY``
        on timeout / both surfaces failing. The losing task is always
        cancelled inside this function — callers do not need to know
        about either surface's underlying lifecycle.

        Race semantics (sharpener #50.1, #50.5):
          - If only one surface is available, that surface's ``wait_for``
            is the only path — same shape as the pre-#50 single-prompt
            behaviour.
          - If both are available, ``asyncio.wait`` with FIRST_COMPLETED
            picks whichever returns first; the loser is cancelled and
            awaited (swallowing ``CancelledError``) so its cleanup
            ``finally`` blocks run before this returns.
        """
        timeout = _timeout_seconds()
        tasks: dict[str, asyncio.Task[str]] = {}
        if tray_available:
            tasks["tray"] = asyncio.create_task(self._prompt(req))
        if voice_fn is not None:
            tasks["voice"] = asyncio.create_task(voice_fn(req))

        if len(tasks) == 1:
            (label, task), = tasks.items()
            try:
                return await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.info(
                    "[consent] Timeout on %s prompt for '%s' (capability=%s) — DENY",
                    label, req.tool_name, req.capability.value,
                )
                return CHOICE_DENY

        # Race both surfaces. The first completed task wins; the other
        # is cancelled so its listener / IPC future / etc. can clean up.
        done, pending = await asyncio.wait(
            set(tasks.values()),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # Loser cleanup must not derail the winner. CancelledError
                # is expected; other exceptions are logged at debug only —
                # they don't affect the user-facing decision.
                pass

        if not done:
            logger.info(
                "[consent] Both prompts timed out for '%s' (capability=%s) — DENY",
                req.tool_name, req.capability.value,
            )
            return CHOICE_DENY

        winner = next(iter(done))
        try:
            return winner.result()
        except Exception:
            logger.exception(
                "[consent] Winner task raised for '%s' (capability=%s) — DENY",
                req.tool_name, req.capability.value,
            )
            return CHOICE_DENY

    def _apply_choice(self, capability: Capability, choice: str) -> Decision:
        """Mutate the ACL based on the user's choice; return the decision.

        Per the sharpener (#4) and ADR-0005:
          - Once: allow exactly this one call; no ACL mutation. A second
            concurrent caller waiting on the same lock re-resolves and
            sees the unchanged ACL → re-prompts.
          - Session: stash an in-RAM SILENT grant; future non-passive
            calls of this class go through silently this session.
          - Persistent: write to profile_acl; survives restart.
          - Deny: refuse this call; no ACL mutation.

        Note: the surface intentionally does NOT call ProfileACL.grant_once
        for the Once button. grant_once stashes a grant consumed by the
        next resolve(), which would silently cover a queued second
        caller — directly contradicting sharpener #4 ("Once = this one
        call, second call re-prompts"). Returning SILENT here allows the
        current call without leaving anything in the once-queue.
        """
        if choice == CHOICE_DENY:
            return Decision.DENY
        if self._acl is None:
            # No ACL bound — we still allow this single call but can't
            # persist the grant. Surfacing this as SILENT lets the
            # orchestrator dispatch.
            logger.warning(
                "[consent] Choice %r on '%s' with no ACL bound — single-call allow",
                choice, capability.value,
            )
            return Decision.SILENT
        if choice == CHOICE_SESSION:
            self._acl.grant_session(capability, Decision.SILENT)
        elif choice == CHOICE_PERSISTENT:
            self._acl.set_persistent_class(capability, Decision.SILENT)
        # CHOICE_ONCE intentionally does nothing here.
        return Decision.SILENT


def is_valid_choice(choice: object) -> bool:
    """True iff a tray-emitted choice string is one of the four valid verbs."""
    return isinstance(choice, str) and choice in _VALID_CHOICES
