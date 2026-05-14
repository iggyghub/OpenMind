"""
Tray consent surface tests — Issue #48.

Covers the ConsentSurface bridge between the orchestrator's ASK decisions
and the tray prompt: fail-closed paths (no subscriber, irreversible,
timeout, ACL-says-DENY), the four user-choice paths (Once/Session/
Persistent/Deny) and how each one mutates (or doesn't mutate) the ACL,
the per-(profile, capability) prompt serialisation rule, and the IPC
payload shape the tray renders.

The end-to-end integration test (Slice 8) drives the same path through
the orchestrator's ``call_tool`` so the full ask → notification →
Persistent → next-call-silent round-trip from issue #48 AC#6 is pinned.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator, Tool, ToolResult
from cerebral.security import (
    CAPABILITY_DESCRIPTION,
    CAPABILITY_LABEL,
    CHOICE_DENY,
    CHOICE_ONCE,
    CHOICE_PERSISTENT,
    CHOICE_SESSION,
    Capability,
    CallFlags,
    ConsentRequest,
    ConsentSurface,
    Decision,
    ProfileACL,
    build_args_preview,
    description_for,
    is_valid_choice,
    label_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm(tmp_path) -> ProfileManager:
    db = tmp_path / "openmind.db"
    return ProfileManager(db_path=db)


@pytest.fixture
def profile(pm: ProfileManager) -> Profile:
    return pm.create(name="Alice", wake_name="felix", voice_id="af_heart")


@pytest.fixture
def acl(pm: ProfileManager, profile: Profile) -> ProfileACL:
    return ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )


class _FakePrompt:
    """A scripted prompt fn: returns the next queued choice per call."""

    def __init__(self, *choices, sleep: float = 0.0) -> None:
        self.choices = list(choices)
        self.sleep = sleep
        self.received: list[ConsentRequest] = []

    async def __call__(self, req: ConsentRequest) -> str:
        self.received.append(req)
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if not self.choices:
            raise AssertionError("no scripted choice for this prompt")
        return self.choices.pop(0)


class _NeverPrompt:
    """A prompt fn that never resolves — drives the timeout path."""

    def __init__(self) -> None:
        self.received: list[ConsentRequest] = []

    async def __call__(self, req: ConsentRequest) -> str:
        self.received.append(req)
        await asyncio.Future()  # forever
        return "deny"


@pytest.fixture
def surface_factory(acl):
    """Build a ConsentSurface bound to the given ACL with optional overrides."""
    def _make(
        prompt_fn,
        *,
        bind_acl: ProfileACL | None = acl,
        has_subscriber: bool = True,
        request_id: str = "req-1",
    ):
        ids = iter([request_id, f"{request_id}-b", f"{request_id}-c"])
        return ConsentSurface(
            prompt_fn=prompt_fn,
            has_subscriber_fn=lambda: has_subscriber,
            acl=bind_acl,
            request_id_fn=lambda: next(ids),
        )
    return _make


# ===========================================================================
# Slice 1 — fail-closed paths (no subscriber, irreversible, timeout)
# ===========================================================================


@pytest.mark.asyncio
async def test_no_subscriber_denies_without_prompting(surface_factory):
    prompt = _FakePrompt()
    s = surface_factory(prompt, has_subscriber=False)
    d = await s.request(Capability.FS_WRITE, "files.write", {})
    assert d is Decision.DENY
    assert prompt.received == []  # never asked


@pytest.mark.asyncio
async def test_irreversible_flag_denies_without_prompting(surface_factory):
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    s = surface_factory(prompt)
    d = await s.request(
        Capability.FS_DELETE, "files.delete", {"path": "/x"},
        flags=CallFlags(irreversible=True),
    )
    # v1: irreversible routes to the modal (#49) — surface fail-closes.
    assert d is Decision.DENY
    assert prompt.received == []


@pytest.mark.asyncio
async def test_timeout_denies_and_does_not_mutate_acl(surface_factory, acl, monkeypatch):
    monkeypatch.setenv("OPENMIND_CONSENT_TIMEOUT_SEC", "0.05")
    prompt = _NeverPrompt()
    s = surface_factory(prompt)
    d = await s.request(Capability.FS_WRITE, "files.write", {})
    assert d is Decision.DENY
    # ACL untouched — no persistent grants, future calls still ASK
    assert acl.list_persistent_grants() == []
    assert acl._resolve_pre_escalation(Capability.FS_WRITE, "files.write") is Decision.ASK


# ===========================================================================
# Slice 2 — the four choice paths
# ===========================================================================


@pytest.mark.asyncio
async def test_deny_choice_returns_deny_and_no_mutation(surface_factory, acl):
    prompt = _FakePrompt(CHOICE_DENY)
    s = surface_factory(prompt)
    d = await s.request(Capability.FS_WRITE, "files.write", {"path": "/x"})
    assert d is Decision.DENY
    assert acl.list_persistent_grants() == []
    assert acl._resolve_pre_escalation(Capability.FS_WRITE, "files.write") is Decision.ASK


@pytest.mark.asyncio
async def test_once_choice_returns_silent_and_no_mutation(surface_factory, acl):
    # Once means "allow this one call, ask again next time" — the surface
    # must NOT call acl.grant_once (that would silently cover the next
    # call too, contradicting sharpener #4's concurrency semantic).
    prompt = _FakePrompt(CHOICE_ONCE)
    s = surface_factory(prompt)
    d = await s.request(Capability.FS_WRITE, "files.write", {})
    assert d is Decision.SILENT
    assert acl.list_persistent_grants() == []
    # The next resolve sees the unchanged ACL → still ASK
    assert acl._resolve_pre_escalation(Capability.FS_WRITE, "files.write") is Decision.ASK


@pytest.mark.asyncio
async def test_session_choice_writes_session_grant(surface_factory, acl):
    prompt = _FakePrompt(CHOICE_SESSION)
    s = surface_factory(prompt)
    d = await s.request(Capability.FS_WRITE, "files.write", {})
    assert d is Decision.SILENT
    # Session grant is RAM-only — no persistent row, but future resolves are silent
    assert acl.list_persistent_grants() == []
    assert acl._resolve_pre_escalation(Capability.FS_WRITE, "files.write") is Decision.SILENT


@pytest.mark.asyncio
async def test_persistent_choice_writes_persistent_grant(surface_factory, acl):
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    s = surface_factory(prompt)
    d = await s.request(Capability.FS_WRITE, "files.write", {})
    assert d is Decision.SILENT
    rows = acl.list_persistent_grants()
    assert len(rows) == 1
    row = rows[0]
    assert row["scope"] == "class"
    assert row["target"] == Capability.FS_WRITE.value
    assert row["policy"] == Decision.SILENT.value
    assert acl._resolve_pre_escalation(Capability.FS_WRITE, "files.write") is Decision.SILENT


# ===========================================================================
# Slice 3 — IPC payload shape (the tray's contract)
# ===========================================================================


def test_consent_request_to_ipc_envelope_shape():
    req = ConsentRequest(
        request_id="abc-123",
        tool_name="files.write_journal_entry",
        capability=Capability.FS_WRITE,
        flags=CallFlags(passive=False, irreversible=False),
        args_preview={"path": "/Users/me/journal.md"},
    )
    payload = req.to_ipc()
    assert payload["type"] == "consent_request"
    # Nested under data to match every other event in main.py (queue_update,
    # plugins_list, profile_loaded, …).
    data = payload["data"]
    assert data["request_id"] == "abc-123"
    assert data["tool_name"] == "files.write_journal_entry"
    assert data["capability"] == "fs_write"
    assert data["capability_label"] == label_for(Capability.FS_WRITE)
    assert data["capability_description"] == description_for(Capability.FS_WRITE)
    assert data["args_preview"] == {"path": "/Users/me/journal.md"}
    assert data["flags"] == {"passive": False, "irreversible": False}


def test_consent_request_passive_flag_passes_through():
    req = ConsentRequest(
        request_id="x", tool_name="t", capability=Capability.FS_WRITE,
        flags=CallFlags(passive=True), args_preview={},
    )
    assert req.to_ipc()["data"]["flags"]["passive"] is True


def test_consent_request_irreversible_flag_passes_through():
    req = ConsentRequest(
        request_id="x", tool_name="t", capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True), args_preview={},
    )
    assert req.to_ipc()["data"]["flags"]["irreversible"] is True


# ===========================================================================
# Slice 4 — args preview truncation
# ===========================================================================


def test_args_preview_empty():
    assert build_args_preview(None) == {}
    assert build_args_preview({}) == {}


def test_args_preview_passes_short_strings():
    args = {"path": "/x", "lines": 12}
    assert build_args_preview(args) == {"path": "/x", "lines": 12}


def test_args_preview_truncates_long_strings():
    long_string = "a" * 500
    preview = build_args_preview({"body": long_string})
    assert preview["body"].endswith("…")
    assert len(preview["body"]) == 201  # 200 chars + ellipsis


def test_args_preview_preserves_scalars():
    args = {"count": 7, "ratio": 0.5, "ok": True, "tag": None}
    assert build_args_preview(args) == args


def test_args_preview_stringifies_containers_and_truncates():
    big = {"k" * 200: "v" * 200}  # repr will exceed 200 chars
    preview = build_args_preview({"obj": big})
    assert preview["obj"].endswith("…") or isinstance(preview["obj"], dict)


def test_args_preview_keys_coerced_to_str():
    preview = build_args_preview({42: "v"})
    assert "42" in preview


# ===========================================================================
# Slice 5 — choice validation
# ===========================================================================


def test_is_valid_choice_accepts_all_four():
    for c in [CHOICE_ONCE, CHOICE_SESSION, CHOICE_PERSISTENT, CHOICE_DENY]:
        assert is_valid_choice(c)


def test_is_valid_choice_rejects_unknowns():
    assert not is_valid_choice("")
    assert not is_valid_choice("yes")
    assert not is_valid_choice(None)
    assert not is_valid_choice(42)
    assert not is_valid_choice("Once")  # case-sensitive


@pytest.mark.asyncio
async def test_unknown_choice_from_prompt_denies(surface_factory):
    # If the tray sends a garbage choice somehow, the surface refuses it
    # rather than risking a grant.
    prompt = _FakePrompt("yes")  # not in the valid set
    s = surface_factory(prompt)
    d = await s.request(Capability.FS_WRITE, "files.write", {})
    assert d is Decision.DENY


# ===========================================================================
# Slice 6 — per-(profile, capability) prompt serialisation
# ===========================================================================


@pytest.mark.asyncio
async def test_concurrent_calls_same_capability_serialize(surface_factory, acl):
    # Two callers for the same class arrive simultaneously. The first
    # one prompts; the second waits on the lock. When the first picks
    # Persistent, the second re-resolves through the ACL and finds a
    # SILENT grant — no second prompt.
    gate_open = asyncio.Event()
    received = []

    async def prompt_fn(req):
        received.append(req)
        await gate_open.wait()
        return CHOICE_PERSISTENT

    s = surface_factory(prompt_fn)

    call_a = asyncio.create_task(
        s.request(Capability.FS_WRITE, "files.write", {})
    )
    # Let A acquire the lock and emit the prompt
    await asyncio.sleep(0.01)
    call_b = asyncio.create_task(
        s.request(Capability.FS_WRITE, "files.write", {})
    )
    # Give B a moment to start; it should be blocked on the lock
    await asyncio.sleep(0.01)
    gate_open.set()

    da, db = await asyncio.gather(call_a, call_b)
    assert da is Decision.SILENT
    assert db is Decision.SILENT
    # Only the first call prompted; the second resolved silently from the ACL
    assert len(received) == 1


@pytest.mark.asyncio
async def test_concurrent_calls_once_choice_reprompts_second(surface_factory, acl):
    # When the first picks Once, the ACL is NOT mutated. The second
    # waiter re-resolves, still sees ASK, and gets its own prompt.
    gate_open = asyncio.Event()
    received = []

    async def prompt_fn(req):
        received.append(req)
        await gate_open.wait()
        return CHOICE_ONCE

    s = surface_factory(prompt_fn)

    call_a = asyncio.create_task(s.request(Capability.FS_WRITE, "t", {}))
    await asyncio.sleep(0.01)
    call_b = asyncio.create_task(s.request(Capability.FS_WRITE, "t", {}))
    await asyncio.sleep(0.01)
    gate_open.set()
    da, db = await asyncio.gather(call_a, call_b)
    assert da is Decision.SILENT
    assert db is Decision.SILENT
    # Both calls received their own prompt
    assert len(received) == 2


@pytest.mark.asyncio
async def test_concurrent_calls_deny_reprompts_second(surface_factory, acl):
    # Deny does not mutate the ACL — the second waiter re-prompts.
    gate_open = asyncio.Event()
    received = []

    async def prompt_fn(req):
        received.append(req)
        await gate_open.wait()
        return CHOICE_DENY

    s = surface_factory(prompt_fn)

    call_a = asyncio.create_task(s.request(Capability.FS_WRITE, "t", {}))
    await asyncio.sleep(0.01)
    call_b = asyncio.create_task(s.request(Capability.FS_WRITE, "t", {}))
    await asyncio.sleep(0.01)
    gate_open.set()
    da, db = await asyncio.gather(call_a, call_b)
    assert da is Decision.DENY
    assert db is Decision.DENY
    assert len(received) == 2


@pytest.mark.asyncio
async def test_concurrent_calls_different_capabilities_run_in_parallel(surface_factory):
    # Per-class locks — a call for FS_WRITE shouldn't block a call for
    # SCREEN_CAPTURE. We simulate by having FS_WRITE's prompt block
    # until SCREEN_CAPTURE's prompt has already completed.
    fs_started     = asyncio.Event()
    screen_finished = asyncio.Event()
    order: list[str] = []

    async def prompt_fn(req):
        if req.capability is Capability.FS_WRITE:
            fs_started.set()
            await screen_finished.wait()  # wait for the other class to finish
            order.append("fs_done")
            return CHOICE_ONCE
        else:  # SCREEN_CAPTURE
            order.append("screen_done")
            screen_finished.set()
            return CHOICE_ONCE

    s = surface_factory(prompt_fn)

    call_a = asyncio.create_task(s.request(Capability.FS_WRITE, "t", {}))
    await fs_started.wait()
    # FS_WRITE is parked inside its prompt. If locks were global, this
    # next call would deadlock; with per-class locks it runs in parallel.
    call_b = asyncio.create_task(s.request(Capability.SCREEN_CAPTURE, "t", {}))

    da, db = await asyncio.wait_for(
        asyncio.gather(call_a, call_b), timeout=2.0,
    )
    assert da is Decision.SILENT and db is Decision.SILENT
    assert order == ["screen_done", "fs_done"]


# ===========================================================================
# Slice 7 — orchestrator integration (call_tool dispatch)
# ===========================================================================


class _StubPlugin:
    """Minimal plugin for orchestrator tests; records every tool call."""

    name = "stub"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return [Tool(name="stub.act", description="stub", plugin=self.name)]

    async def call_tool(self, tool_name, args):
        self.calls.append((tool_name, args))
        return ToolResult(content="ok")


@pytest.mark.asyncio
async def test_orchestrator_routes_ask_to_consent_surface(acl):
    plugin = _StubPlugin()
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    surface = ConsentSurface(
        prompt_fn=prompt,
        has_subscriber_fn=lambda: True,
        acl=acl,
        request_id_fn=lambda: "r1",
    )
    orc = MCPOrchestrator(acl=acl, consent=surface)
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))
    # FS_WRITE defaults to ASK in DEFAULT_POLICY → routes to surface.
    result = await orc.call_tool(
        "stub.act", {"path": "/x"},
        capability=Capability.FS_WRITE,
    )
    assert not result.is_error
    assert plugin.calls == [("stub.act", {"path": "/x"})]
    # Persistent grant was written — a second call goes silent.
    result2 = await orc.call_tool(
        "stub.act", {"path": "/y"},
        capability=Capability.FS_WRITE,
    )
    assert not result2.is_error
    assert plugin.calls[-1] == ("stub.act", {"path": "/y"})
    # Only one prompt fired across both calls
    assert len(prompt.received) == 1


@pytest.mark.asyncio
async def test_orchestrator_deny_choice_blocks_dispatch(acl):
    plugin = _StubPlugin()
    prompt = _FakePrompt(CHOICE_DENY)
    surface = ConsentSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "r1",
    )
    orc = MCPOrchestrator(acl=acl, consent=surface)
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))
    result = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_WRITE,
    )
    assert result.is_error
    assert plugin.calls == []  # plugin never invoked


@pytest.mark.asyncio
async def test_orchestrator_no_consent_surface_keeps_pre_48_fail_closed(acl):
    plugin = _StubPlugin()
    orc = MCPOrchestrator(acl=acl)  # no consent surface
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))
    result = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_WRITE,
    )
    assert result.is_error
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_orchestrator_silent_default_skips_consent(acl):
    # FS_READ is SILENT by default — the consent surface is never asked.
    plugin = _StubPlugin()
    prompt = _FakePrompt()  # would assert if called
    surface = ConsentSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "r1",
    )
    orc = MCPOrchestrator(acl=acl, consent=surface)
    orc.register(plugin, required_capabilities=frozenset({"fs_read"}))
    result = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_READ,
    )
    assert not result.is_error
    assert plugin.calls == [("stub.act", {})]
    assert prompt.received == []


@pytest.mark.asyncio
async def test_orchestrator_fail_closed_no_subscriber(acl):
    plugin = _StubPlugin()
    prompt = _FakePrompt()  # never called
    surface = ConsentSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: False,  # no tray
        acl=acl, request_id_fn=lambda: "r1",
    )
    orc = MCPOrchestrator(acl=acl, consent=surface)
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))
    result = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_WRITE,
    )
    assert result.is_error  # fail-closed
    assert plugin.calls == []
    assert prompt.received == []


# ===========================================================================
# Slice 8 — full round-trip (issue #48 AC#6)
# ===========================================================================


@pytest.mark.asyncio
async def test_full_round_trip_ask_to_persistent_to_silent_next_call(acl, pm, profile):
    """The AC: ask → notification → "Persistent" → next call silent.

    Drives the round-trip through the orchestrator with a real ProfileACL
    so the SQLite write is exercised — a second `ProfileACL` instance
    on the same DB sees the grant and resolves silently.
    """
    plugin = _StubPlugin()
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    surface = ConsentSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "r1",
    )
    orc = MCPOrchestrator(acl=acl, consent=surface)
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    # First call: prompts, user picks Persistent, dispatches.
    r1 = await orc.call_tool("stub.act", {}, capability=Capability.FS_WRITE)
    assert not r1.is_error
    assert len(prompt.received) == 1
    assert prompt.received[0].capability is Capability.FS_WRITE
    assert prompt.received[0].tool_name == "stub.act"

    # Rebuild the ACL — clears RAM grants, simulates Cerebral restart.
    # The persistent grant in profile_acl is what matters.
    fresh_acl = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    orc.set_acl(fresh_acl)
    # New ACL means new locks — and the consent surface needs to be
    # rebound. The orchestrator's set_acl handles that.

    # Second call resolves silently via the persistent grant — no prompt.
    r2 = await orc.call_tool("stub.act", {}, capability=Capability.FS_WRITE)
    assert not r2.is_error
    assert plugin.calls == [("stub.act", {}), ("stub.act", {})]
    assert len(prompt.received) == 1  # no second prompt


# ===========================================================================
# Slice 9 — set_acl swap clears the surface's locks
# ===========================================================================


@pytest.mark.asyncio
async def test_set_acl_swaps_target_and_clears_locks(acl, pm):
    prompt = _FakePrompt(CHOICE_PERSISTENT, CHOICE_PERSISTENT)
    surface = ConsentSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "r1",
    )
    # First user grants persistent on FS_WRITE
    d = await surface.request(Capability.FS_WRITE, "t", {})
    assert d is Decision.SILENT
    # Swap to a second profile
    p2 = pm.create(name="Bob", wake_name="felix", voice_id="af_heart")
    acl2 = ProfileACL(
        profile_id=p2.id, profile_manager=pm,
        defaults_snapshot=p2.acl_defaults_snapshot,
    )
    surface.set_acl(acl2)
    assert surface.acl is acl2
    # Bob's profile has no grant yet — must prompt
    d2 = await surface.request(Capability.FS_WRITE, "t", {})
    assert d2 is Decision.SILENT
    assert len(prompt.received) == 2  # both profiles prompted


@pytest.mark.asyncio
async def test_set_acl_none_means_no_acl_mutation_but_single_allow(acl):
    prompt = _FakePrompt(CHOICE_SESSION)
    surface = ConsentSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        acl=None, request_id_fn=lambda: "r1",
    )
    d = await surface.request(Capability.FS_WRITE, "t", {})
    # No ACL bound — still allow this single call (orchestrator dispatches)
    assert d is Decision.SILENT


# ===========================================================================
# Slice 10 — env-var timeout configuration
# ===========================================================================


@pytest.mark.asyncio
async def test_timeout_env_var_overrides_default(surface_factory, monkeypatch):
    monkeypatch.setenv("OPENMIND_CONSENT_TIMEOUT_SEC", "0.02")
    prompt = _NeverPrompt()
    s = surface_factory(prompt)
    import time
    t0 = time.monotonic()
    d = await s.request(Capability.FS_WRITE, "t", {})
    elapsed = time.monotonic() - t0
    assert d is Decision.DENY
    assert elapsed < 1.0  # bounded by the env override, not the default 30s


@pytest.mark.asyncio
async def test_timeout_env_var_garbage_falls_back_to_default(surface_factory, monkeypatch):
    # We can't actually wait 30s in a test; instead, verify the env
    # value is rejected and the surface uses something positive.
    monkeypatch.setenv("OPENMIND_CONSENT_TIMEOUT_SEC", "not-a-number")
    from cerebral.security.consent import _timeout_seconds, DEFAULT_TIMEOUT_SECONDS
    assert _timeout_seconds() == DEFAULT_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_timeout_env_var_zero_or_negative_falls_back(monkeypatch):
    from cerebral.security.consent import _timeout_seconds, DEFAULT_TIMEOUT_SECONDS
    monkeypatch.setenv("OPENMIND_CONSENT_TIMEOUT_SEC", "0")
    assert _timeout_seconds() == DEFAULT_TIMEOUT_SECONDS
    monkeypatch.setenv("OPENMIND_CONSENT_TIMEOUT_SEC", "-5")
    assert _timeout_seconds() == DEFAULT_TIMEOUT_SECONDS
