"""Tests for the interrupt_turn IPC handler (S20 / #303).

Verifies that:
1. interrupt_turn cancels a running _active_turn_task.
2. interrupt_turn is a no-op when no task is active (doesn't crash).
3. TTSEngine.stop() is called on interrupt.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

async def _long_running():
    """Coroutine that blocks until cancelled."""
    await asyncio.sleep(9999)


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_interrupt_turn_cancels_active_task():
    """interrupt_turn IPC must cancel the active turn task."""
    import cerebral.main as main_mod

    saved_task = main_mod._active_turn_task
    saved_tts_stop = main_mod._tts.stop

    try:
        task = asyncio.create_task(_long_running())
        main_mod._active_turn_task = task
        # Give the task a moment to start.
        await asyncio.sleep(0)

        stop_called = []
        main_mod._tts.stop = lambda: stop_called.append(True)

        await main_mod._handle_message({"type": "interrupt_turn"})

        assert task.cancelled() or task.cancelling() > 0, "task should be cancelled"
        assert stop_called, "TTS stop() should have been called"
    finally:
        main_mod._active_turn_task = saved_task
        main_mod._tts.stop = saved_tts_stop
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def test_interrupt_turn_noop_when_no_task():
    """interrupt_turn must not raise when _active_turn_task is None."""
    import cerebral.main as main_mod

    saved_task = main_mod._active_turn_task
    saved_tts_stop = main_mod._tts.stop

    try:
        main_mod._active_turn_task = None
        main_mod._tts.stop = lambda: None

        # Must not raise.
        await main_mod._handle_message({"type": "interrupt_turn"})
    finally:
        main_mod._active_turn_task = saved_task
        main_mod._tts.stop = saved_tts_stop


async def test_process_command_records_interruption(monkeypatch):
    """When _process_command is cancelled, it records a turn_interrupted event."""
    import cerebral.main as main_mod

    recorded = []
    broadcast = []

    async def fake_record(kind, content, **_kw):
        recorded.append((kind, content))

    async def fake_broadcast(event):
        broadcast.append(event)

    async def fake_memory_preamble(_t):
        return ""

    async def fake_chain_run(*_a, **_kw):
        await asyncio.sleep(9999)

    chain_mock = MagicMock()
    chain_mock.run = fake_chain_run

    saved = {
        "_record_turn": main_mod._record_turn,
        "_broadcast": main_mod._broadcast,
        "_memory_preamble": main_mod._memory_preamble,
    }

    monkeypatch.setattr(main_mod, "_record_turn", fake_record)
    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_memory_preamble", fake_memory_preamble)

    from cerebral.llm.chain_engine import ChainEngine
    with patch.object(ChainEngine, "run", fake_chain_run):
        task = asyncio.create_task(
            main_mod._process_command("hello", speak=False)
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    interrupt_records = [c for _k, c in recorded if c.get("event") == "turn_interrupted"]
    assert interrupt_records, "turn_interrupted system event must be recorded"

    interrupt_broadcasts = [e for e in broadcast if e.get("type") == "turn_interrupted"]
    assert interrupt_broadcasts, "turn_interrupted must be broadcast"

    passive_broadcasts = [e for e in broadcast if e.get("type") == "passive"]
    assert passive_broadcasts, "passive must be broadcast after interruption"
