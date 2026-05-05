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
from mcp.orchestrator import MCPOrchestrator
from memory.manager import MemoryManager
from passive.extractor import FiveW1HExtractor
from action_queue.manager import QueueManager
from insights.engine import InsightsEngine
from tts.engine import TTSEngine
from environment.context import EnvironmentContext

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
_orc = MCPOrchestrator()
_queue = QueueManager()
_extractor = FiveW1HExtractor(_router)
_env = EnvironmentContext()


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


def _env_context_event() -> dict:
    return {"type": "env_context_update", "data": {"context": _env.get_context()}}


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
        logger.info("[cerebral] Profile created: %s (id=%d)", p.name, p.id)
        await _broadcast(_profile_event(p))
        await _broadcast(_profiles_list_event())

    elif t == "switch_profile":
        pid = msg.get("data", {}).get("id")
        if pid is not None:
            p = _pm.get(int(pid))
            if p:
                _pm.set_active(p.id)
                _active_profile = p
                logger.info("[cerebral] Switched to profile: %s", p.name)
                await _broadcast(_profile_event(p))

    elif t == "delete_profile":
        pid = msg.get("data", {}).get("id")
        if pid is not None:
            _pm.delete(int(pid))
            logger.info("[cerebral] Profile %d deleted", pid)
            _active_profile = _pm.get_active()
            if _active_profile:
                await _broadcast(_profile_event(_active_profile))
            else:
                await _broadcast({"type": "first_run"})
            await _broadcast(_profiles_list_event())

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
                await _broadcast({"type": "model_switched", "data": {"model_id": model_id}})
            except ValueError as exc:
                logger.warning("[cerebral] switch_model failed: %s", exc)

    elif t == "list_tools":
        await _broadcast({"type": "tools_list", "data": {"tools": _orc.tools_for_llm}})

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
        # Execute the associated tool if one was recorded
        if item.tool_name:
            result = await _orc.call_tool(item.tool_name, item.tool_args or {})
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
    await _send(websocket, _env_context_event())

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

    _orc.discover_plugins(_PLUGINS_DIR)
    logger.info("[cerebral] MCP orchestrator ready — %d tool(s) registered", len(_orc.list_tools()))

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
