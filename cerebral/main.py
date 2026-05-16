"""
Cerebral — the central Python backend process for OpenMind / Felix.

Starts:
  - SQLite ProfileManager (profiles auto-created on first run via tray prompt)
  - Kokoro TTS engine (speaks responses via the active profile's voice)
  - A WebSocket IPC server (ws://localhost:7766) for the Node.js tray frontend
  - The always-on audio pipeline (Vosk + faster-whisper)
    Skip if Vosk model is missing — run `python scripts/download_models.py` first.
"""

import asyncio
import json
import logging
import os
import sys

import websockets
from websockets.asyncio.server import serve

from pathlib import Path

from audio.pipeline import AudioPipeline, DEFAULT_SIGNAL_WORDS
from bridge.openclaw import ChannelBridge
from db.profiles import Profile, ProfileManager
from llm.router import ModelRouter, ModelUnavailableError
from mcp.orchestrator import MCPOrchestrator, ToolResult
from memory.manager import MemoryManager
from passive.extractor import FiveW1HExtractor
from action_queue.manager import QueueManager
from insights.engine import InsightsEngine
from tts.engine import TTSEngine
from environment.context import EnvironmentContext
from cerebral.security import (
    CAPABILITY_DESCRIPTION,
    CAPABILITY_LABEL,
    CallFlags,
    Capability,
    ConsentRequest,
    ConsentSurface,
    Decision,
    ModalRequest,
    ModalSurface,
    ProfileACL,
    VoiceConsent,
    is_valid_choice,
    is_valid_modal_choice,
)

_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("websockets").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

HOST = "localhost"
PORT = 7766

_connected: set = set()
_shutdown = asyncio.Event()

_pm = ProfileManager()
_active_profile: Profile | None = _pm.get_active()
_tts = TTSEngine()
_router = ModelRouter()
# Restore the user's last-chosen model if the active profile remembers one
# (issue #37). If the saved id isn't in the current backends — e.g. that
# Ollama model was uninstalled — fall back silently to whatever default
# the router auto-picked.
if _active_profile and _active_profile.active_model:
    try:
        _router.switch_model(_active_profile.active_model)
        logger.info("[cerebral] Restored model from profile: %s", _active_profile.active_model)
    except ValueError:
        logger.warning(
            "[cerebral] Saved model '%s' not in current backends — using default %s",
            _active_profile.active_model,
            _router.active_model,
        )
_orc = MCPOrchestrator()
_queue = QueueManager()
_extractor = FiveW1HExtractor(_router)
_env = EnvironmentContext()


def _new_plugin_flag_for_tool(tool_name: str) -> bool:
    """ACL hook (Issue #51): translate a tool name → owning plugin → flag.

    When the plugin currently carries the 'new plugin' flag, ProfileACL
    skips its session/persistent/per-tool bypass layers so every call on
    that plugin's tools asks fresh. The flag is set by the builder on
    install and cleared by the user via the Permissions UI (#53).
    """
    plugin = _orc.plugin_for_tool(tool_name)
    return bool(plugin) and _pm.get_plugin_new_flag(plugin)


def _build_acl(profile) -> ProfileACL:
    """Construct a ProfileACL bound to the given profile's snapshot."""
    return ProfileACL(
        profile_id=profile.id,
        profile_manager=_pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
        new_plugin_flag_for_tool=_new_plugin_flag_for_tool,
    )


# Wire the active profile's ACL into the orchestrator (Issue #45). On
# profile switch we rebuild it so once/session grants are cleared and the
# new profile's persistent grants are consulted from then on.
if _active_profile:
    _orc.set_acl(_build_acl(_active_profile))


# ── Consent surface (Issue #48) ───────────────────────────────────────────────
#
# When the ACL resolves to ASK, the orchestrator calls into the consent
# surface which:
#   1. Sends a `consent_request` event to all connected tray clients.
#   2. Awaits a matching `consent_response` keyed by request_id.
#   3. Returns SILENT (allow) or DENY (deny / timeout / no-subscriber).
#
# Pending responses live here so the IPC dispatcher (a synchronous-looking
# function called per inbound message) can fulfil them via asyncio futures.
_pending_consents: dict[str, asyncio.Future[str]] = {}


def _consent_has_subscriber() -> bool:
    """True when at least one tray client is connected on the IPC channel.

    Used by the consent surface to short-circuit to DENY without emitting a
    prompt event into the void (ADR-0005 fail-closed rule)."""
    return len(_connected) > 0


async def _consent_prompt(req: ConsentRequest) -> str:
    """Bridge from `ConsentSurface` to the tray over WebSocket IPC.

    Emits the `consent_request` event, registers a future under the
    request_id, and awaits the matching `consent_response`. The surface
    wraps this in `asyncio.wait_for(..., timeout=...)` so timeouts are
    handled there (we just clean up the pending future on cancel)."""
    fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
    _pending_consents[req.request_id] = fut
    try:
        await _broadcast(req.to_ipc())
        return await fut
    finally:
        _pending_consents.pop(req.request_id, None)


_consent_surface = ConsentSurface(
    prompt_fn=_consent_prompt,
    has_subscriber_fn=_consent_has_subscriber,
    acl=_orc.acl,
)
_orc.set_consent_surface(_consent_surface)


# ── Irreversible-flag modal surface (Issue #49) ──────────────────────────────
#
# When a call sets `flags.irreversible=True` AND the gate/ACL didn't
# already DENY, the orchestrator routes through this surface instead of
# the consent surface — even when a Session/Persistent grant would
# otherwise cover the class. Acceptance is one-shot, never persisted
# (ADR-0005 / AC#4); no ACL mutation lives here.
_pending_modals: dict[str, asyncio.Future[str]] = {}


async def _modal_prompt(req: ModalRequest) -> str:
    """Bridge from `ModalSurface` to the tray over WebSocket IPC.

    Emits the `irreversible_modal_request` event, registers a future
    under the request_id, and awaits the matching
    `irreversible_modal_response`. The surface wraps this in
    `asyncio.wait_for(..., timeout=...)` so timeouts are handled there;
    we just clean up the pending future on cancel."""
    fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
    _pending_modals[req.request_id] = fut
    try:
        await _broadcast(req.to_ipc())
        return await fut
    finally:
        _pending_modals.pop(req.request_id, None)


_modal_surface = ModalSurface(
    prompt_fn=_modal_prompt,
    has_subscriber_fn=_consent_has_subscriber,
)
_orc.set_modal_surface(_modal_surface)


async def _bridge_process(transcript: str, history: list[dict]) -> str:
    """Process an inbound channel message through the LLM. Reuses the same
    router used by voice wakes; tools are made available via the same
    orchestrator. History is folded into a simple prefixed prompt — good
    enough for short multi-turn conversations on Telegram/Discord/etc."""
    if history:
        recent = history[-8:]
        context = "\n".join(
            f"{('User' if turn['role'] == 'user' else 'Felix')}: {turn['text']}"
            for turn in recent
        )
        prompt = f"Conversation so far:\n{context}\n\nUser: {transcript}\nFelix:"
    else:
        prompt = transcript
    return await _router.complete(prompt, task_type="chat")


_bridge = ChannelBridge(
    process_fn=_bridge_process,
    ws_url=os.environ.get("OPENCLAW_WS_URL", "ws://localhost:3000/agent/stream"),
    outbound_url=os.environ.get("OPENCLAW_REPLY_URL", "http://localhost:3000/agent/reply"),
    api_key=os.environ.get("OPENCLAW_API_KEY", ""),
)

def _get_memory() -> MemoryManager | None:
    """Return a MemoryManager for the active profile, or None if no profile loaded."""
    if _active_profile is None:
        return None
    return MemoryManager(profile_id=_active_profile.id)


def _get_insights() -> InsightsEngine | None:
    """Return an InsightsEngine for the active profile, or None if no profile loaded."""
    if _active_profile is None:
        return None
    return InsightsEngine(profile_id=_active_profile.id)


# Issue #79 — wire _get_memory() into the memory MCP plugin so the LLM can
# call memory_remember/recall/forget against the active profile's Chroma
# collection. Same lifecycle as the modal-surface wiring: orchestrator
# discovers + create()s the plugin at module load; this setter binds the
# factory before any LLM call can land.
import plugins.memory as _memory_plugin
_memory_plugin.set_memory_factory(_get_memory)


# ── IPC helpers ───────────────────────────────────────────────────────────────

async def _broadcast(event: dict) -> None:
    if not _connected:
        return
    payload = json.dumps(event)
    await asyncio.gather(*(ws.send(payload) for ws in _connected), return_exceptions=True)


async def _send(websocket, event: dict) -> None:
    try:
        await websocket.send(json.dumps(event))
    except websockets.exceptions.ConnectionClosed:
        pass


def _profile_event(profile: Profile) -> dict:
    return {"type": "profile_loaded", "data": profile.to_dict()}


def _profiles_list_event() -> dict:
    return {
        "type": "profiles_list",
        "data": {"profiles": [p.to_dict() for p in _pm.list_all()]},
    }


def _voices_list_event() -> dict:
    return {"type": "voices_list", "data": {"voices": _tts.list_voices()}}


def _queue_update_event() -> dict:
    return {
        "type": "queue_update",
        "data": {"items": [i.to_dict() for i in _queue.get_pending()]},
    }


def _insights_update_event() -> dict:
    eng = _get_insights()
    insights = eng.list_insights() if eng else []
    return {"type": "insights_update", "data": {"insights": [i.to_dict() for i in insights]}}


def _memory_update_event() -> dict:
    mgr = _get_memory()
    memories = mgr.list_all() if mgr else []
    return {"type": "memory_update", "data": {"memories": [
        {"id": m.id, "fact": m.fact, "created_at": m.created_at} for m in memories
    ]}}


def _env_context_event() -> dict:
    return {"type": "env_context_update", "data": {"context": _env.get_context()}}


def _models_list_event() -> dict:
    return {
        "type": "models_list",
        "data": {
            "models": _router.list_models(),
            "active": _router.active_model,
            "last": _router.last_model,
            "active_is_cloud": _router.active_is_cloud,
            "task_models": _router.task_models(),
        },
    }


def _permissions_state_event() -> dict:
    """Snapshot of the active profile's ACL state for the Permissions UI (#53).

    Payload:
      capability_vocabulary — the closed 16-class enum, paired with the
        user-language label, the one-sentence description (#48 labels.py)
        and the day-1 default policy. The tray renders one row per entry
        in the Capabilities tab and is forbidden by ADR-0005 from inventing
        new classes (AC#7).
      class_defaults — capability → policy from the profile's frozen
        snapshot (#45). The tray uses this as the row's fallback when
        no persistent_class_grant overrides it.
      persistent_class_grants — capability → policy. The user's overrides
        on the snapshot, persisted in the profile_acl table.
      persistent_tool_overrides — tool_name → policy. Per-tool overrides
        from the same table.
      session_class_grants — capability → policy. RAM-only grants from
        the consent surface's Session button; cleared on profile switch.
      shell_exec_unlocked — bool. Has the user opted into editing the
        shell_exec row? (default False; one-way flip via unlock_shell_exec)
      profile_id — for sanity-checking on the tray side that a stale
        broadcast doesn't overwrite a freshly-switched profile.

    Returns an empty payload (everything zeroed) when no profile is loaded;
    the tray's Permissions menu entry should be hidden in that state.
    """
    if _active_profile is None or _orc.acl is None:
        return {
            "type": "permissions_state",
            "data": {
                "profile_id": None,
                "capability_vocabulary": _capability_vocabulary(),
                "class_defaults": {},
                "persistent_class_grants": {},
                "persistent_tool_overrides": {},
                "session_class_grants": {},
                "shell_exec_unlocked": False,
            },
        }
    acl = _orc.acl
    persistent = acl.list_persistent_grants()
    class_grants: dict[str, str] = {}
    tool_overrides: dict[str, str] = {}
    for row in persistent:
        if row["scope"] == "class":
            class_grants[row["target"]] = row["policy"]
        elif row["scope"] == "tool":
            tool_overrides[row["target"]] = row["policy"]
    session_grants = {
        row["capability"]: row["policy"] for row in acl.list_session_grants()
    }
    return {
        "type": "permissions_state",
        "data": {
            "profile_id": _active_profile.id,
            "capability_vocabulary": _capability_vocabulary(),
            "class_defaults": dict(_active_profile.acl_defaults_snapshot),
            "persistent_class_grants": class_grants,
            "persistent_tool_overrides": tool_overrides,
            "session_class_grants": session_grants,
            "shell_exec_unlocked": _active_profile.shell_exec_unlocked,
        },
    }


def _capability_vocabulary() -> list[dict]:
    """Closed 16-class vocabulary projected for the tray (#53 sharpener #6).

    The tray is forbidden from inventing classes; this list IS the source
    of truth it renders. Stable order: enum-declaration order, which is
    the ADR-0005 ordering grouped by sensitivity bucket.
    """
    from cerebral.security import DEFAULT_POLICY
    return [
        {
            "value":       cap.value,
            "label":       CAPABILITY_LABEL[cap],
            "description": CAPABILITY_DESCRIPTION[cap],
            "default":     DEFAULT_POLICY[cap].value,
        }
        for cap in Capability
    ]


def _plugins_list_event() -> dict:
    """Snapshot of the orchestrator's plugin registry for the tray.

    Each entry pairs a plugin's name with:
      - the REQUIRED_CAPABILITIES it declared (Issue #44)
      - its inspectability mark — "inspected" or "trusted" (Issue #46)
        so the tray can render the red "trusted, unverified" badge on
        plugins loaded from plugins/_trusted/.

    The companion `errors` list carries plugins the orchestrator refused at
    load time (forbidden patterns, non-conforming paths, missing
    REQUIRED_CAPABILITIES, …) so the tray can render *why* a plugin isn't
    there alongside the ones that are.
    """
    registered = []
    for plugin_name in sorted(_orc._plugins):
        caps = _orc.required_capabilities_for(plugin_name)
        registered.append({
            "name": plugin_name,
            "required_capabilities": sorted(caps) if caps is not None else None,
            "inspectability": _orc.inspectability_for(plugin_name),
            # Issue #51 — the tray surfaces a "new plugin" badge on
            # builder-installed plugins whose flag is still set. Cleared
            # via the Permissions UI (#53).
            "new_plugin_flag": _pm.get_plugin_new_flag(plugin_name),
        })
    return {
        "type": "plugins_list",
        "data": {
            "plugins": registered,
            "errors": _orc.registration_errors,
        },
    }


async def _pulse_back_to_passive(delay: float = 1.2) -> None:
    """Brief 'thinking' pulse on the visualiser, then back to passive."""
    await asyncio.sleep(delay)
    await _broadcast({"type": "passive", "data": {"status": "running"}})


# ── TTS helpers ───────────────────────────────────────────────────────────────

async def _speak(text: str) -> None:
    """Speak using the active profile's voice; fires tts_speaking/tts_done events."""
    voice_id = _active_profile.voice_id if _active_profile else None
    await _broadcast({"type": "tts_speaking", "data": {"text": text, "voice_id": voice_id}})
    await _tts.speak(text, voice_id)
    await _broadcast({"type": "tts_done", "data": {}})


# ── Message dispatcher ────────────────────────────────────────────────────────

async def _handle_message(msg: dict) -> None:
    global _active_profile
    t = msg.get("type")

    if t == "shutdown":
        logger.info("[cerebral] Shutdown requested by tray")
        _shutdown.set()

    elif t == "create_profile":
        d = msg.get("data", {})
        p = _pm.create(
            name=d.get("name", "User"),
            wake_name=d.get("wake_name", "felix"),
            pronunciation_guide=d.get("pronunciation_guide", ""),
            voice_id=d.get("voice_id", "af_heart"),
            voice_sample=d.get("voice_sample", ""),
            wake_sample=d.get("wake_sample", ""),
        )
        _pm.set_active(p.id)
        _active_profile = p
        _orc.set_acl(_build_acl(p))
        logger.info("[cerebral] Profile created: %s (id=%d)", p.name, p.id)
        await _broadcast(_profile_event(p))
        await _broadcast(_profiles_list_event())
        await _broadcast(_permissions_state_event())

    elif t == "switch_profile":
        pid = msg.get("data", {}).get("id")
        if pid is not None:
            p = _pm.get(int(pid))
            if p:
                _pm.set_active(p.id)
                _active_profile = p
                # Rebuild the ACL on profile switch — Issue #45 / ADR-0005
                # mandates that once + session grants clear on switch.
                _orc.set_acl(_build_acl(p))
                logger.info("[cerebral] Switched to profile: %s", p.name)
                await _broadcast(_profile_event(p))
                # Issue #53 — re-read ACL state for the switched profile.
                # The session-grant store is RAM-only and the just-built
                # ACL has none, so the tray's session-grants sub-panel
                # will correctly empty out.
                await _broadcast(_permissions_state_event())

    elif t == "delete_profile":
        pid = msg.get("data", {}).get("id")
        if pid is not None:
            _pm.delete(int(pid))
            logger.info("[cerebral] Profile %d deleted", pid)
            _active_profile = _pm.get_active()
            if _active_profile:
                _orc.set_acl(_build_acl(_active_profile))
                await _broadcast(_profile_event(_active_profile))
            else:
                _orc.set_acl(None)
                await _broadcast({"type": "first_run"})
            await _broadcast(_profiles_list_event())
            await _broadcast(_permissions_state_event())

    elif t == "list_profiles":
        await _broadcast(_profiles_list_event())

    elif t == "list_voices":
        await _broadcast(_voices_list_event())

    elif t == "set_voice":
        # Update the active profile's voice_id; next speak() call picks it up.
        voice_id = msg.get("data", {}).get("voice_id")
        if voice_id and _active_profile:
            _pm.update_voice(_active_profile.id, voice_id)
            _active_profile = _pm.get(_active_profile.id)
            logger.info("[cerebral] Voice updated to %s for profile %s", voice_id, _active_profile.name)
            await _broadcast(_profile_event(_active_profile))

    elif t == "switch_model":
        model_id = msg.get("data", {}).get("model_id")
        if model_id:
            try:
                _router.switch_model(model_id)
                logger.info("[cerebral] Model router switched to %s", model_id)
                # Persist the choice so it survives restart (issue #37).
                if _active_profile:
                    _pm.update_active_model(_active_profile.id, model_id)
                    _active_profile = _pm.get(_active_profile.id)
                await _broadcast({
                    "type": "model_switched",
                    "data": {"model_id": model_id, "is_cloud": _router.active_is_cloud},
                })
                await _broadcast({"type": "model_switching", "data": {"model_id": model_id}})
                await _broadcast(_models_list_event())
                asyncio.create_task(_pulse_back_to_passive())
            except ValueError as exc:
                logger.warning("[cerebral] switch_model failed: %s", exc)

    elif t == "list_models":
        await _broadcast(_models_list_event())

    elif t == "refresh_models":
        # Re-query Ollama and rebuild the local-backend slice of the router.
        # Cloud entries stay untouched. Issue #37.
        new_ids = _router.refresh_local_backends()
        logger.info("[cerebral] Refreshed installed Ollama models: %s", new_ids)
        await _broadcast(_models_list_event())

    elif t == "set_task_model":
        d = msg.get("data", {})
        task_type = d.get("task_type", "")
        model_id = d.get("model_id")
        if not task_type:
            return
        try:
            _router.set_task_model(task_type, model_id)
            logger.info("[cerebral] Task '%s' mapped to %s", task_type, model_id)
            await _broadcast(_models_list_event())
        except ValueError as exc:
            logger.warning("[cerebral] set_task_model failed: %s", exc)

    elif t == "list_tools":
        await _broadcast({"type": "tools_list", "data": {"tools": _orc.tools_for_llm}})

    elif t == "list_plugins":
        await _broadcast(_plugins_list_event())

    elif t == "list_permissions":
        # Issue #53 — Permissions UI requesting a fresh state snapshot.
        # The same payload is broadcast on connect alongside other state
        # events, but a re-open of the Permissions window asks for a fresh
        # read in case the user changed profiles in between.
        await _broadcast(_permissions_state_event())

    elif t == "set_class_policy":
        # Issue #53 — Capabilities tab toggle. {capability, decision}.
        d = msg.get("data") or {}
        cap_value = (d.get("capability") or "").strip()
        decision = (d.get("decision") or "").strip()
        if not cap_value or not decision:
            logger.warning("[cerebral] set_class_policy missing capability/decision")
            return
        if _orc.acl is None or _active_profile is None:
            logger.warning("[cerebral] set_class_policy with no active profile")
            return
        try:
            cap = Capability(cap_value)
            dec = Decision(decision)
        except ValueError as exc:
            logger.warning("[cerebral] set_class_policy invalid value: %s", exc)
            return
        # shell_exec is locked until the user explicitly opts in (#53 AC#2).
        if cap is Capability.SHELL_EXEC and not _active_profile.shell_exec_unlocked:
            logger.warning(
                "[cerebral] set_class_policy refused: shell_exec is locked for profile %d",
                _active_profile.id,
            )
            return
        # Default-matching writes still create a row — the user's
        # explicit click is meaningful even when it equals the snapshot
        # default. Revoking back to the snapshot is a separate IPC
        # (revoke_class_policy) so the toggle's three-state UI maps
        # cleanly to one verb per user action.
        _orc.acl.set_persistent_class(cap, dec)
        logger.info(
            "[cerebral] set_class_policy %s=%s for profile %d",
            cap.value, dec.value, _active_profile.id,
        )
        await _broadcast(_permissions_state_event())

    elif t == "revoke_class_policy":
        # Issue #53 — clears a persistent class grant so the snapshot
        # default applies again. Used when the user resets a Capabilities
        # row to its inherited default.
        d = msg.get("data") or {}
        cap_value = (d.get("capability") or "").strip()
        if not cap_value or _orc.acl is None:
            return
        try:
            cap = Capability(cap_value)
        except ValueError:
            return
        _orc.acl.revoke_persistent_class(cap)
        logger.info("[cerebral] revoke_class_policy %s", cap.value)
        await _broadcast(_permissions_state_event())

    elif t == "set_tool_override":
        # Issue #53 — Tools tab dropdown. {tool, decision}. decision of
        # "inherit" clears the override (revoke_tool_override).
        d = msg.get("data") or {}
        tool_name = (d.get("tool") or "").strip()
        decision = (d.get("decision") or "").strip()
        if not tool_name or not decision or _orc.acl is None:
            logger.warning("[cerebral] set_tool_override missing field")
            return
        if decision == "inherit":
            _orc.acl.revoke_tool_override(tool_name)
            logger.info("[cerebral] set_tool_override %s=inherit (revoked)", tool_name)
        else:
            try:
                dec = Decision(decision)
            except ValueError as exc:
                logger.warning("[cerebral] set_tool_override invalid decision: %s", exc)
                return
            _orc.acl.set_tool_override(tool_name, dec)
            logger.info("[cerebral] set_tool_override %s=%s", tool_name, dec.value)
        await _broadcast(_permissions_state_event())

    elif t == "revoke_session_grant":
        # Issue #53 — Capabilities tab session-grant Revoke button.
        d = msg.get("data") or {}
        cap_value = (d.get("capability") or "").strip()
        if not cap_value or _orc.acl is None:
            return
        try:
            cap = Capability(cap_value)
        except ValueError:
            return
        revoked = _orc.acl.revoke_session(cap)
        logger.info(
            "[cerebral] revoke_session_grant %s (existed=%s)", cap.value, revoked,
        )
        await _broadcast(_permissions_state_event())

    elif t == "unlock_shell_exec":
        # Issue #53 — one-way flip. The Permissions UI shows a confirmation
        # modal first; this handler trusts the click as the confirmation.
        if _active_profile is None:
            logger.warning("[cerebral] unlock_shell_exec with no active profile")
            return
        _pm.unlock_shell_exec(_active_profile.id)
        _active_profile = _pm.get(_active_profile.id)
        logger.info(
            "[cerebral] shell_exec unlocked for profile %s", _active_profile.name,
        )
        await _broadcast(_permissions_state_event())

    elif t == "clear_new_plugin_flag":
        # Issue #51 — the Permissions UI's "I've reviewed this plugin"
        # affordance flips new_plugin to 0 and re-broadcasts plugins_list
        # so the tray's badge drops on every connected client. The flag is
        # the only thing standing between this plugin's tools and the
        # ACL's normal session/persistent bypasses (#53 owns the UI).
        d = msg.get("data") or {}
        plugin_name = (d.get("name") or "").strip()
        if not plugin_name:
            logger.warning("[cerebral] clear_new_plugin_flag missing 'name'")
            return
        _pm.set_plugin_new_flag(plugin_name, False)
        logger.info("[cerebral] Cleared new_plugin flag for %r", plugin_name)
        await _broadcast(_plugins_list_event())

    elif t == "consent_response":
        d = msg.get("data") or {}
        request_id = d.get("request_id")
        choice = d.get("choice")
        if not request_id:
            logger.warning("[cerebral] consent_response missing request_id")
            return
        fut = _pending_consents.get(request_id)
        if fut is None:
            # Late arrival — surface already timed out and cleaned up.
            logger.info(
                "[cerebral] consent_response for unknown request_id=%s (ignored)",
                request_id,
            )
            return
        if not is_valid_choice(choice):
            logger.warning(
                "[cerebral] consent_response invalid choice=%r for %s",
                choice, request_id,
            )
            if not fut.done():
                fut.set_result("deny")
            return
        if not fut.done():
            fut.set_result(choice)

    elif t == "irreversible_modal_response":
        # Issue #49 — Accept dispatches the call, Cancel refuses. The
        # ModalSurface treats any non-accept choice as DENY, so a garbled
        # message is safely refused without us second-guessing here.
        d = msg.get("data") or {}
        request_id = d.get("request_id")
        choice = d.get("choice")
        if not request_id:
            logger.warning("[cerebral] irreversible_modal_response missing request_id")
            return
        fut = _pending_modals.get(request_id)
        if fut is None:
            logger.info(
                "[cerebral] irreversible_modal_response for unknown request_id=%s (ignored)",
                request_id,
            )
            return
        if not is_valid_modal_choice(choice):
            logger.warning(
                "[cerebral] irreversible_modal_response invalid choice=%r for %s",
                choice, request_id,
            )
            if not fut.done():
                fut.set_result("cancel")
            return
        if not fut.done():
            fut.set_result(choice)

    elif t == "call_tool":
        d = msg.get("data", {})
        tool_name = d.get("name", "")
        tool_args = d.get("args", {})
        result = await _orc.call_tool(tool_name, tool_args)
        await _broadcast({
            "type": "tool_result",
            "data": {"name": tool_name, "content": result.content, "is_error": result.is_error},
        })

    elif t == "list_queue":
        await _broadcast(_queue_update_event())

    elif t == "approve_item":
        item_id = msg.get("data", {}).get("item_id", "")
        item = _queue.approve_item(item_id)
        if item is None:
            logger.warning("[cerebral] approve_item: unknown id %s", item_id)
            return
        logger.info("[cerebral] Queue item approved: %s", item.title)
        eng = _get_insights()
        if eng:
            eng.record_signal("approve", item.title, tool_name=item.tool_name)
            new_insight = eng.maybe_create_insight(item.title, tool_name=item.tool_name)
            if new_insight:
                logger.info("[cerebral] New insight: %s", new_insight.description)
                await _broadcast(_insights_update_event())
        # Execute the associated tool if one was recorded.
        #
        # Issue #52 — queue-originated calls run with ``passive=True`` so the
        # ACL escalates SILENT → ASK and ASK → DENY, defeating any session or
        # persistent grant the user holds on the class. The check runs across
        # ALL of the plugin's declared capabilities (AND semantics) before
        # dispatching exactly once. ``check_capabilities`` IS the gate for
        # this call — we dispatch via ``call_tool`` without a capability so
        # the consent surface isn't prompted a second time.
        if item.tool_name:
            plugin_name = _orc.plugin_for_tool(item.tool_name)
            caps = (
                _orc.required_capabilities_for(plugin_name)
                if plugin_name is not None
                else None
            )
            if caps:
                decision = await _orc.check_capabilities(
                    item.tool_name, caps, CallFlags(passive=True),
                )
            else:
                # No declared capabilities (legacy register() path or
                # capability-free tool) → no gate constraint; dispatch.
                decision = Decision.SILENT

            if decision is Decision.SILENT:
                # ``check_capabilities`` already routed through ACL +
                # consent. Dispatch without re-invoking the gate inside
                # ``call_tool`` (capability=None, flags=None).
                result = await _orc.call_tool(
                    item.tool_name, item.tool_args or {},
                )
            else:
                # ASK is never returned (check_capabilities collapses it to
                # SILENT or DENY); treat everything non-SILENT as a refusal.
                logger.info(
                    "[cerebral] Queue approval denied: %s (decision=%s)",
                    item.tool_name, decision.value,
                )
                result = ToolResult(
                    content=(
                        f"Denied: '{item.tool_name}' was refused by the "
                        f"capability gate (decision: {decision.value})"
                    ),
                    is_error=True,
                )

            logger.info("[cerebral] Tool result for %s: %s", item.tool_name, result.content[:80])
            await _broadcast({
                "type": "queue_item_result",
                "data": {
                    "item_id": item_id,
                    "result": result.content,
                    "is_error": result.is_error,
                },
            })
        await _broadcast(_queue_update_event())

    elif t == "remember":
        fact = msg.get("data", {}).get("fact", "")
        mem = _get_memory()
        if fact and mem:
            memory_id = await mem.remember(fact)
            await _broadcast({"type": "memory_stored", "data": {"id": memory_id, "fact": fact}})

    elif t == "recall":
        query = msg.get("data", {}).get("query", "")
        mem = _get_memory()
        if query and mem:
            memories = await mem.recall(query)
            await _broadcast({
                "type": "memory_results",
                "data": {"memories": [
                    {"id": m.id, "fact": m.fact, "distance": m.distance}
                    for m in memories
                ]},
            })

    elif t == "forget":
        memory_id = msg.get("data", {}).get("memory_id", "")
        mem = _get_memory()
        if memory_id and mem:
            ok = await mem.forget(memory_id)
            await _broadcast({"type": "memory_forgotten", "data": {"id": memory_id, "ok": ok}})

    elif t == "dismiss_item":
        item_id = msg.get("data", {}).get("item_id", "")
        item = _queue.get_item(item_id)
        ok = _queue.dismiss_item(item_id)
        if not ok:
            logger.warning("[cerebral] dismiss_item: unknown id %s", item_id)
            return
        logger.info("[cerebral] Queue item dismissed: %s", item_id)
        if item:
            eng = _get_insights()
            if eng:
                eng.record_signal("dismiss", item.title, tool_name=item.tool_name)
                eng.maybe_create_insight(item.title, tool_name=item.tool_name)
        await _broadcast(_queue_update_event())

    elif t == "list_insights":
        await _broadcast(_insights_update_event())

    elif t == "delete_insight":
        insight_id = msg.get("data", {}).get("insight_id", "")
        eng = _get_insights()
        ok = eng.delete_insight(insight_id) if eng else False
        if ok:
            await _broadcast(_insights_update_event())
        await _broadcast({"type": "insight_deleted", "data": {"id": insight_id, "ok": ok}})

    elif t == "pin_insight":
        insight_id = msg.get("data", {}).get("insight_id", "")
        eng = _get_insights()
        ok = eng.pin_insight(insight_id) if eng else False
        if ok:
            await _broadcast(_insights_update_event())

    elif t == "edit_insight":
        d = msg.get("data", {})
        insight_id = d.get("insight_id", "")
        description = d.get("description", "")
        eng = _get_insights()
        ok = eng.edit_insight(insight_id, description) if eng else False
        if ok:
            await _broadcast(_insights_update_event())

    elif t == "list_memories":
        await _broadcast(_memory_update_event())

    elif t == "edit_memory":
        d = msg.get("data", {})
        mgr = _get_memory()
        ok = await mgr.edit(d.get("memory_id", ""), d.get("fact", "")) if mgr else False
        if ok:
            await _broadcast(_memory_update_event())

    elif t == "delete_memory":
        mgr = _get_memory()
        ok = await mgr.forget(msg.get("data", {}).get("memory_id", "")) if mgr else False
        if ok:
            await _broadcast(_memory_update_event())

    elif t == "set_camera_enabled":
        enabled = msg.get("data", {}).get("enabled", False)
        if enabled:
            _env.enable_camera()
        else:
            _env.disable_camera()
        logger.info("[cerebral] Camera %s", "enabled" if enabled else "disabled")
        await _broadcast(_env_context_event())

    elif t == "get_env_context":
        await _broadcast(_env_context_event())


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def _ws_handler(websocket) -> None:
    _connected.add(websocket)
    logger.info("[cerebral] Client connected  (%d total)", len(_connected))

    # Greet new connection with current state
    if _active_profile:
        await _send(websocket, _profile_event(_active_profile))
    else:
        await _send(websocket, {"type": "first_run"})
    await _send(websocket, _profiles_list_event())
    await _send(websocket, _voices_list_event())
    await _send(websocket, _queue_update_event())
    await _send(websocket, _insights_update_event())
    await _send(websocket, _memory_update_event())
    await _send(websocket, _env_context_event())
    await _send(websocket, _models_list_event())
    await _send(websocket, _plugins_list_event())
    await _send(websocket, _permissions_state_event())

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle_message(msg)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _connected.discard(websocket)
        logger.info("[cerebral] Client disconnected (%d remaining)", len(_connected))


# ── Audio callbacks ────────────────────────────────────────────────────────────

async def _on_passive(transcript: str) -> None:
    """Called when a signal word triggers a passive 5W1H extraction pass."""
    logger.info("[cerebral] Passive trigger — transcript: %r", transcript)
    action = await _extractor.extract(transcript, env_context=_env.get_context())
    if action is None:
        logger.info("[cerebral] Passive extraction discarded (low confidence or parse error)")
        return
    item = _queue.add_item(
        title=action.title,
        summary=action.summary,
    )
    logger.info("[cerebral] Queued passive action: %s (confidence=%.2f)", item.title, action.confidence)
    await _broadcast(_queue_update_event())


async def _on_wake(transcript: str) -> None:
    logger.info("[cerebral] Wake event — transcript: %r", transcript)
    await _broadcast({"type": "wake", "data": {"transcript": transcript}})
    asyncio.create_task(_process_command(transcript))


async def _process_command(transcript: str) -> None:
    """Call the LLM with the transcribed command and speak the response."""
    tools = _orc.tools_for_llm
    await _broadcast({"type": "thinking"})
    try:
        response = await _router.complete(transcript, task_type="chat")
        logger.info("[cerebral] LLM response (%d tools available): %r", len(tools), response[:80])
        await _speak(response)
    except ModelUnavailableError as exc:
        logger.error("[cerebral] Model unavailable: %s", exc)
        await _speak("Sorry, I can't reach the language model right now.")
    except Exception as exc:
        logger.error("[cerebral] Unexpected error during LLM call: %s", exc)
        await _speak("Something went wrong. Please try again.")
    await _broadcast({"type": "passive", "data": {"status": "running"}})


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _attach_builder_plugin() -> None:
    """Wire the auto-discovered builder meta-plugin to the live orchestrator.

    `BuilderPlugin.create()` returns a parked stand-in during discovery (no
    orchestrator handle yet); here we hand it the real orchestrator, plus a
    tight pip-allowlist + LLM hook so its tools become callable.
    """
    parked = _orc._plugins.get("builder")
    if parked is None or not hasattr(parked, "attach"):
        return
    try:
        from plugins.builder import _ParkedBuilderPlugin  # type: ignore
    except Exception:  # pragma: no cover - import guard
        return
    if not isinstance(parked, _ParkedBuilderPlugin):
        return

    def _llm_fn(description: str, suggested_name: str | None = None) -> dict:
        # Intentionally minimal — until #6 model router exposes structured-output
        # generation, builder_create surfaces a clear error rather than guessing.
        raise NotImplementedError(
            "Plugin generation requires the model router's structured-output "
            "path, which is not yet wired. Set BUILDER_LLM_FN before calling "
            "builder_create, or call BuilderPlugin directly from a test."
        )

    parked.attach(
        _orc,
        llm_fn=_llm_fn,
        pip_allowlist=("requests", "httpx", "aiohttp", "beautifulsoup4", "lxml"),
        profile_manager=_pm,
    )
    logger.info("[cerebral] Plugin builder attached (pip_allowlist=5 packages)")


async def _heartbeat_loop(audio_active: bool) -> None:
    while not _shutdown.is_set():
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        if _shutdown.is_set():
            break
        profile_name = _active_profile.name if _active_profile else None
        await _broadcast({
            "type": "heartbeat",
            "data": {
                "status": "running",
                "audio": audio_active,
                "tts": _tts.ready,
                "profile": profile_name,
                "model": _router.active_model,
                "last_model": _router.last_model,
                "active_is_cloud": _router.active_is_cloud,
                "queue_pending": len(_queue.get_pending()),
                "env": _env.get_context().get("city") or "unknown",
                "bridge": _bridge.running,
            },
        })
        logger.info(
            "[cerebral] Heartbeat sent (profile=%s, tts=%s, model=%s)",
            profile_name, _tts.ready, _router.active_model,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    loop = asyncio.get_running_loop()

    pipeline: AudioPipeline | None = None
    try:
        pipeline = AudioPipeline(
            on_wake=_on_wake,
            on_passive=_on_passive,
            signal_words=list(DEFAULT_SIGNAL_WORDS),
        )
        await loop.run_in_executor(None, lambda: pipeline.start(loop))
        audio_active = True
    except FileNotFoundError as exc:
        logger.warning("[cerebral] Audio pipeline unavailable: %s", exc)
        audio_active = False

    if not _tts.ready:
        logger.warning("[cerebral] TTS unavailable — install kokoro: pip install kokoro soundfile")

    # Voice consent surface (Issue #50). Constructed AFTER the audio
    # pipeline starts so VoiceConsent can reach the loaded Vosk model
    # via ``pipeline.vosk_model``. Wired onto the consent surface only
    # when both TTS and the pipeline are ready — per AC#6, missing either
    # is a graceful degradation to tray-only, not an error.
    if pipeline is not None and _tts.ready:
        _voice_consent = VoiceConsent(
            tts=_tts,
            audio_pipeline=pipeline,
            voice_id_fn=lambda: _active_profile.voice_id if _active_profile else None,
            plugin_name_for_tool=_orc.plugin_for_tool,
        )
        if _voice_consent.ready:
            _consent_surface.set_voice_prompt_fn(_voice_consent.prompt)
            logger.info("[cerebral] Voice consent wired (Vosk + Kokoro ready)")
        else:
            logger.info(
                "[cerebral] Voice consent not ready — tray-only consent surface"
            )
    else:
        logger.info(
            "[cerebral] Voice consent skipped (audio_pipeline=%s, tts.ready=%s) — "
            "tray-only consent surface",
            pipeline is not None, _tts.ready,
        )

    _orc.discover_plugins(_PLUGINS_DIR)
    _attach_builder_plugin()
    logger.info("[cerebral] MCP orchestrator ready — %d tool(s) registered", len(_orc.list_tools()))
    for err in _orc.registration_errors:
        logger.warning(
            "[cerebral] Plugin refused: %s — %s (%s)",
            err["plugin_name"], err["reason"], err["detail"],
        )

    await _env.refresh_location()
    logger.info("[cerebral] Environment context: %s", _env.get_context())

    if _active_profile:
        logger.info("[cerebral] Active profile: %s (id=%d)", _active_profile.name, _active_profile.id)
    else:
        logger.info("[cerebral] No profiles found — will prompt on tray connection")

    bridge_task = asyncio.create_task(_bridge.start())

    logger.info("[cerebral] Starting IPC server on ws://%s:%d", HOST, PORT)
    async with serve(_ws_handler, HOST, PORT):
        logger.info("[cerebral] Listening - waiting for tray connection")
        heartbeat = asyncio.create_task(_heartbeat_loop(audio_active))
        await _shutdown.wait()
        heartbeat.cancel()

    if pipeline is not None:
        pipeline.stop()

    await _bridge.stop()
    try:
        await asyncio.wait_for(bridge_task, timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        bridge_task.cancel()

    logger.info("[cerebral] Shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n[cerebral] Interrupted - shutting down.")
        sys.exit(0)
