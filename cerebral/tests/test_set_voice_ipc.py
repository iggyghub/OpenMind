"""
set_voice IPC handler tests.

Run-and-fix campaign iteration 5 (2026-06-13): the dispatcher used to
silently persist ANY voice_id to the active profile, including ids not
in the Kokoro catalogue. The bug surfaced during run-and-fix smoke
('[cerebral] Voice updated to no_such_voice for profile Alice') -- the
next speak() call would then push the unknown voice into KPipeline and
fail at synth time, leaving the tray showing 'voice updated' but TTS
silently broken.

Sibling switch_model / set_task_model handlers already reject unknown
ids; set_voice was the inconsistent one. The fix mirrors their pattern.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile


@pytest.fixture
def voice_rig():
    """Patch the bits of cerebral.main set_voice touches: profile manager,
    broadcast, active profile, and the tts engine's voice catalogue."""
    import cerebral.main as main_mod

    sent: list[dict] = []
    update_calls: list[tuple[int, str]] = []

    async def fake_broadcast(event: dict) -> None:
        sent.append(event)

    class FakePM:
        def __init__(self) -> None:
            self.voice_id = "af_heart"

        def update_voice(self, profile_id: int, voice_id: str) -> None:
            update_calls.append((profile_id, voice_id))
            self.voice_id = voice_id

        def get(self, profile_id: int) -> Profile:
            return Profile(name="Test", id=profile_id, voice_id=self.voice_id)

    class FakeTTS:
        def list_voices(self) -> list[dict]:
            return [
                {"id": "af_heart", "name": "Heart"},
                {"id": "am_adam",  "name": "Adam"},
            ]

    saved = {
        "_active_profile": main_mod._active_profile,
        "_broadcast":      main_mod._broadcast,
        "_pm":             main_mod._pm,
        "_tts":            main_mod._tts,
    }

    main_mod._active_profile = Profile(name="Test", id=1, voice_id="af_heart")
    main_mod._broadcast      = fake_broadcast
    main_mod._pm             = FakePM()
    main_mod._tts            = FakeTTS()

    class Rig:
        def __init__(self) -> None:
            self.module       = main_mod
            self.sent         = sent
            self.update_calls = update_calls

        async def handle(self, msg: dict) -> None:
            await main_mod._handle_message(msg)

    try:
        yield Rig()
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)


async def test_set_voice_persists_known_voice(voice_rig):
    await voice_rig.handle({"type": "set_voice", "data": {"voice_id": "am_adam"}})
    assert voice_rig.update_calls == [(1, "am_adam")]
    assert any(e["type"] == "profile_loaded" for e in voice_rig.sent)


async def test_set_voice_rejects_unknown_voice(voice_rig):
    await voice_rig.handle(
        {"type": "set_voice", "data": {"voice_id": "no_such_voice"}}
    )
    assert voice_rig.update_calls == []
    assert not any(e["type"] == "profile_loaded" for e in voice_rig.sent)


async def test_set_voice_missing_voice_id_noop(voice_rig):
    await voice_rig.handle({"type": "set_voice", "data": {}})
    assert voice_rig.update_calls == []


async def test_set_voice_no_active_profile_noop(voice_rig):
    voice_rig.module._active_profile = None
    await voice_rig.handle({"type": "set_voice", "data": {"voice_id": "am_adam"}})
    assert voice_rig.update_calls == []
