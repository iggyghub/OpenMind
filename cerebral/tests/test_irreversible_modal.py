"""
Irreversible-flag modal surface tests — Issue #49.

When a tool call sets ``CallFlags.irreversible=True``, the orchestrator
routes through a *separate* surface from the consent prompt: a two-button
Accept / Cancel modal that **never** mutates the ACL (AC#4 — acceptance
is one-shot, never persisted) and fires even when a Session/Persistent
grant for that class is in place (AC#2). This module pins those slices:
fail-closed paths, the two choice paths, IPC envelope shape, orchestrator
routing (including the regression that ``ConsentSurface.request`` is
*never* reached for an irreversible call), and the no-ACL-mutation
invariant across the persistent-grant scenarios.
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
    CHOICE_ACCEPT,
    CHOICE_CANCEL,
    CHOICE_PERSISTENT,
    Capability,
    CallFlags,
    ConsentSurface,
    Decision,
    ModalRequest,
    ModalSurface,
    ProfileACL,
    description_for,
    is_valid_modal_choice,
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
    """Scripted prompt fn: returns the next queued choice per call."""

    def __init__(self, *choices, sleep: float = 0.0) -> None:
        self.choices = list(choices)
        self.sleep = sleep
        self.received: list[ModalRequest] = []

    async def __call__(self, req: ModalRequest) -> str:
        self.received.append(req)
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if not self.choices:
            raise AssertionError("no scripted choice for this prompt")
        return self.choices.pop(0)


class _NeverPrompt:
    """A prompt fn that never resolves — drives the timeout path."""

    def __init__(self) -> None:
        self.received: list[ModalRequest] = []

    async def __call__(self, req: ModalRequest) -> str:
        self.received.append(req)
        await asyncio.Future()  # forever
        return CHOICE_CANCEL


@pytest.fixture
def modal_factory():
    """Build a ModalSurface with optional overrides for each fail-closed knob."""
    def _make(
        prompt_fn,
        *,
        has_subscriber: bool = True,
        request_id: str = "modal-1",
    ):
        ids = iter([request_id, f"{request_id}-b", f"{request_id}-c"])
        return ModalSurface(
            prompt_fn=prompt_fn,
            has_subscriber_fn=lambda: has_subscriber,
            request_id_fn=lambda: next(ids),
        )
    return _make


# ===========================================================================
# Slice 1 — fail-closed paths (no subscriber, timeout)
# ===========================================================================


@pytest.mark.asyncio
async def test_no_subscriber_denies_without_prompting(modal_factory):
    prompt = _FakePrompt()
    m = modal_factory(prompt, has_subscriber=False)
    d = await m.request(
        Capability.FS_DELETE, "files.delete", {"path": "/x"},
        flags=CallFlags(irreversible=True),
    )
    assert d is Decision.DENY
    assert prompt.received == []  # never asked


@pytest.mark.asyncio
async def test_timeout_denies(modal_factory, monkeypatch):
    monkeypatch.setenv("OPENMIND_CONSENT_TIMEOUT_SEC", "0.05")
    prompt = _NeverPrompt()
    m = modal_factory(prompt)
    d = await m.request(
        Capability.FS_DELETE, "files.delete", {"path": "/x"},
        flags=CallFlags(irreversible=True),
    )
    assert d is Decision.DENY
    # The prompt was emitted (it just never came back) — distinct from the
    # no-subscriber path where nothing is emitted.
    assert len(prompt.received) == 1


# ===========================================================================
# Slice 2 — the two choice paths (Accept / Cancel) + unknown
# ===========================================================================


@pytest.mark.asyncio
async def test_accept_returns_silent(modal_factory):
    prompt = _FakePrompt(CHOICE_ACCEPT)
    m = modal_factory(prompt)
    d = await m.request(
        Capability.FS_DELETE, "files.delete", {"path": "/x"},
        flags=CallFlags(irreversible=True),
    )
    assert d is Decision.SILENT


@pytest.mark.asyncio
async def test_cancel_returns_deny(modal_factory):
    prompt = _FakePrompt(CHOICE_CANCEL)
    m = modal_factory(prompt)
    d = await m.request(
        Capability.FS_DELETE, "files.delete", {"path": "/x"},
        flags=CallFlags(irreversible=True),
    )
    assert d is Decision.DENY


@pytest.mark.asyncio
async def test_unknown_choice_denies(modal_factory):
    # If the tray sends a stray verb (e.g. one of the consent surface's
    # four), the modal refuses rather than risking dispatch on an
    # irreversible call.
    prompt = _FakePrompt(CHOICE_PERSISTENT)
    m = modal_factory(prompt)
    d = await m.request(
        Capability.FS_DELETE, "files.delete", {"path": "/x"},
        flags=CallFlags(irreversible=True),
    )
    assert d is Decision.DENY


# ===========================================================================
# Slice 3 — Accept dispatches but never mutates an ACL (AC#4)
# ===========================================================================


@pytest.mark.asyncio
async def test_modal_does_not_carry_an_acl(modal_factory):
    # The surface has no acl attribute, no set_acl method, no grant_*
    # call — its sole job is to translate a user gesture into one
    # Decision. The orchestrator does not bind an ACL to it.
    prompt = _FakePrompt(CHOICE_ACCEPT)
    m = modal_factory(prompt)
    assert not hasattr(m, "acl")
    assert not hasattr(m, "set_acl")


@pytest.mark.asyncio
async def test_accept_does_not_mutate_acl_via_orchestrator(acl):
    # Even when the orchestrator has a fully-wired ACL, Accept dispatches
    # without leaving any persistent or session grant behind. The next
    # irreversible call must prompt again — there is no "remember for the
    # session" option, by design.
    plugin = _StubPlugin()
    prompt = _FakePrompt(CHOICE_ACCEPT, CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt,
        has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    flags = CallFlags(irreversible=True)
    r1 = await orc.call_tool(
        "stub.act", {"path": "/x"},
        capability=Capability.FS_DELETE, flags=flags,
    )
    assert not r1.is_error
    # No ACL mutation — no persistent rows, the gate still says ASK for
    # fs_delete (so the second call would re-route ASK → modal because
    # of irreversible).
    assert acl.list_persistent_grants() == []

    # Second call: the modal must fire again. Two prompts, two dispatches.
    r2 = await orc.call_tool(
        "stub.act", {"path": "/y"},
        capability=Capability.FS_DELETE, flags=flags,
    )
    assert not r2.is_error
    assert len(prompt.received) == 2


# ===========================================================================
# Slice 4 — IPC envelope shape (the tray's contract)
# ===========================================================================


def test_modal_request_to_ipc_envelope_shape():
    req = ModalRequest(
        request_id="abc-123",
        tool_name="files.delete",
        capability=Capability.FS_DELETE,
        flags=CallFlags(passive=False, irreversible=True),
        args_preview={"path": "/Users/me/notes.md"},
    )
    payload = req.to_ipc()
    assert payload["type"] == "irreversible_modal_request"
    data = payload["data"]
    assert data["request_id"] == "abc-123"
    assert data["tool_name"] == "files.delete"
    assert data["capability"] == "fs_delete"
    assert data["capability_label"] == label_for(Capability.FS_DELETE)
    assert data["capability_description"] == description_for(Capability.FS_DELETE)
    assert data["args_preview"] == {"path": "/Users/me/notes.md"}
    assert data["flags"] == {"passive": False, "irreversible": True}


def test_modal_request_passive_flag_passes_through():
    req = ModalRequest(
        request_id="x", tool_name="t", capability=Capability.FS_DELETE,
        flags=CallFlags(passive=True, irreversible=True), args_preview={},
    )
    assert req.to_ipc()["data"]["flags"]["passive"] is True


def test_modal_request_args_preview_truncates_long_strings():
    # The modal shares ``build_args_preview`` truncation with the consent
    # surface — same 200-char per-value limit.
    long = "a" * 500
    req = ModalRequest(
        request_id="x", tool_name="t", capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
        args_preview={"body": long[:200] + "…"},
    )
    data = req.to_ipc()["data"]
    assert data["args_preview"]["body"].endswith("…")


# ===========================================================================
# Slice 5 — choice vocabulary
# ===========================================================================


def test_is_valid_modal_choice_accepts_both_buttons():
    assert is_valid_modal_choice(CHOICE_ACCEPT)
    assert is_valid_modal_choice(CHOICE_CANCEL)


def test_is_valid_modal_choice_rejects_consent_surface_verbs():
    # The four consent-surface verbs are NOT valid modal choices.
    for verb in ("once", "session", "persistent", "deny"):
        assert not is_valid_modal_choice(verb)


def test_is_valid_modal_choice_rejects_unknown_and_non_str():
    assert not is_valid_modal_choice("")
    assert not is_valid_modal_choice(None)
    assert not is_valid_modal_choice(42)
    assert not is_valid_modal_choice("Accept")  # case-sensitive


# ===========================================================================
# Slice 6 — orchestrator integration (irreversible routing)
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
async def test_orchestrator_routes_irreversible_to_modal(acl):
    plugin = _StubPlugin()
    prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {"path": "/x"},
        capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert not r.is_error
    assert plugin.calls == [("stub.act", {"path": "/x"})]
    assert len(prompt.received) == 1


@pytest.mark.asyncio
async def test_orchestrator_modal_cancel_blocks_dispatch(acl):
    plugin = _StubPlugin()
    prompt = _FakePrompt(CHOICE_CANCEL)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert r.is_error
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_irreversible_fires_even_with_persistent_grant(acl):
    """AC#2 — the headline regression for #49.

    The ACL has a Persistent SILENT grant for fs_delete (the user
    previously clicked "Always allow"). A non-irreversible call would
    resolve to SILENT and dispatch. With ``irreversible=True``, the
    modal must STILL fire — the grant does not bypass it.
    """
    plugin = _StubPlugin()
    acl.set_persistent_class(Capability.FS_DELETE, Decision.SILENT)
    # Confirm the grant is in place — non-irreversible would resolve silent.
    assert acl.resolve(Capability.FS_DELETE, "stub.act", None) is Decision.SILENT

    prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {"path": "/x"},
        capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert not r.is_error
    # Modal fired despite the persistent grant.
    assert len(prompt.received) == 1
    assert plugin.calls == [("stub.act", {"path": "/x"})]


@pytest.mark.asyncio
async def test_irreversible_fires_even_with_session_grant(acl):
    """AC#2 sibling — same as above but with a RAM-only session grant."""
    plugin = _StubPlugin()
    acl.grant_session(Capability.FS_DELETE, Decision.SILENT)
    assert acl.resolve(Capability.FS_DELETE, "stub.act", None) is Decision.SILENT

    prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert not r.is_error
    assert len(prompt.received) == 1


@pytest.mark.asyncio
async def test_irreversible_with_acl_deny_skips_modal(acl):
    """Sharpener #2: if the ACL/gate already DENY'd, the modal does not
    fire — refusal short-circuits before the user is asked.

    Drives this with SHELL_EXEC (DENY by default policy) so the ACL's
    resolve returns DENY before the irreversible-routing rule applies.
    """
    plugin = _StubPlugin()
    prompt = _FakePrompt()  # would assert if called
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"shell_exec"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.SHELL_EXEC,
        flags=CallFlags(irreversible=True),
    )
    assert r.is_error
    assert plugin.calls == []
    assert prompt.received == []  # modal never asked


@pytest.mark.asyncio
async def test_irreversible_never_reaches_consent_surface(acl):
    """Regression: the orchestrator must route irreversible to the modal
    surface, not the consent surface. After #49 the consent surface no
    longer carries the irreversible-as-DENY stub; this test pins that
    no irreversible call lands there at all.
    """
    plugin = _StubPlugin()
    consent_prompt_calls: list[object] = []

    async def consent_prompt(req):
        consent_prompt_calls.append(req)
        return "deny"

    consent = ConsentSurface(
        prompt_fn=consent_prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "c1",
    )
    modal = ModalSurface(
        prompt_fn=_FakePrompt(CHOICE_ACCEPT),
        has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, consent=consent, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert not r.is_error
    # The consent surface was never asked.
    assert consent_prompt_calls == []


@pytest.mark.asyncio
async def test_no_modal_surface_keeps_irreversible_fail_closed(acl):
    """No modal wired and the orchestrator still refuses an irreversible
    call rather than falling back to the consent surface. This preserves
    the pre-#49 invariant that an unwired Cerebral cannot grant
    irreversible-class capabilities.
    """
    plugin = _StubPlugin()
    consent_calls = []

    async def consent_prompt(req):
        consent_calls.append(req)
        return "persistent"

    consent = ConsentSurface(
        prompt_fn=consent_prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "c1",
    )
    orc = MCPOrchestrator(acl=acl, consent=consent)  # no modal
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert r.is_error
    assert plugin.calls == []
    assert consent_calls == []


@pytest.mark.asyncio
async def test_non_irreversible_still_routes_to_consent_not_modal(acl):
    """Sanity: non-irreversible calls still go through the consent
    surface, not the modal. The modal is only for ``irreversible=True``.
    """
    plugin = _StubPlugin()
    consent_prompt = _FakePrompt(CHOICE_PERSISTENT)
    modal_prompt = _FakePrompt()  # would assert if called
    consent = ConsentSurface(
        prompt_fn=consent_prompt, has_subscriber_fn=lambda: True,
        acl=acl, request_id_fn=lambda: "c1",
    )
    modal = ModalSurface(
        prompt_fn=modal_prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, consent=consent, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_write"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_WRITE,
        # No irreversible flag.
    )
    assert not r.is_error
    assert modal_prompt.received == []
    assert len(consent_prompt.received) == 1


@pytest.mark.asyncio
async def test_modal_fail_closed_no_subscriber_via_orchestrator(acl):
    plugin = _StubPlugin()
    prompt = _FakePrompt()  # would assert if called
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: False,  # tray gone
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert r.is_error
    assert plugin.calls == []
    assert prompt.received == []


# ===========================================================================
# Slice 7 — set_modal_surface late-binding (mirrors set_consent_surface)
# ===========================================================================


@pytest.mark.asyncio
async def test_voice_consent_never_invoked_for_irreversible(acl):
    """Issue #50 / AC#7 — the voice consent path must not fire for
    ``irreversible=True`` calls. The orchestrator's ``call_tool`` ladder
    routes irreversible to the modal *before* reaching the consent
    surface, so the surface's ``voice_prompt_fn`` is never asked.

    Pinning the invariant here (full orchestrator round-trip with all
    three surfaces wired) is the right belt-and-suspenders — adding a
    defensive check inside the voice path would just hide a future
    routing bug.
    """
    plugin = _StubPlugin()
    voice_calls: list[object] = []

    async def voice_fn(req):
        voice_calls.append(req)
        return "once"  # CHOICE_ONCE, would normally allow

    consent_prompt = _FakePrompt()  # would assert if called

    consent = ConsentSurface(
        prompt_fn=consent_prompt,
        has_subscriber_fn=lambda: True,
        acl=acl,
        request_id_fn=lambda: "c1",
        voice_prompt_fn=voice_fn,
    )
    modal_prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=modal_prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc = MCPOrchestrator(acl=acl, consent=consent, modal=modal)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    r = await orc.call_tool(
        "stub.act", {"path": "/x"},
        capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert not r.is_error
    assert plugin.calls == [("stub.act", {"path": "/x"})]
    # Modal accepted; voice + consent prompts were never reached.
    assert len(modal_prompt.received) == 1
    assert voice_calls == []
    assert consent_prompt.received == []


# ===========================================================================
# Slice 8 — per-tool `Tool.irreversible` declaration (Issue #139)
#
# After #139 a tool can mark itself irreversible at the schema level. The
# orchestrator ORs the declaration into CallFlags at dispatch so the modal
# fires even when the caller passes flags=None. The 22 tests above stay
# green unchanged — they all pass flags=CallFlags(irreversible=True)
# explicitly, which the merge leaves alone. The tests here add the new
# declaration-driven dispatch path.
# ===========================================================================


@pytest.mark.asyncio
async def test_declared_irreversible_routes_to_modal_via_call_tool(acl):
    """End-to-end: a Tool with irreversible=True triggers the modal even
    when the caller passes flags=None — the #139 headline behaviour."""
    prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )

    class _IrreversiblePlugin:
        name = "mail"

        def list_tools(self):
            return [Tool(
                name="send", description="d", plugin=self.name,
                irreversible=True,
            )]

        async def call_tool(self, tool_name, args):
            return ToolResult(content="sent")

    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(
        _IrreversiblePlugin(),
        required_capabilities=frozenset({"external_data_write"}),
    )

    r = await orc.call_tool(
        "send", {"to": "x"},
        capability=Capability.EXTERNAL_DATA_WRITE,
        # No flags — declaration alone drives the modal.
    )
    assert not r.is_error
    assert r.content == "sent"
    assert len(prompt.received) == 1


@pytest.mark.asyncio
async def test_declared_irreversible_fails_closed_without_modal(acl):
    """No modal wired + declared irreversible → DENY without dispatch.
    Preserves the pre-#139 invariant from #49: an unwired Cerebral
    cannot grant irreversible-class capabilities, regardless of whether
    the flag came from the declaration or the caller."""

    class _IrreversiblePlugin:
        name = "mail"

        def list_tools(self):
            return [Tool(
                name="send", description="d", plugin=self.name,
                irreversible=True,
            )]

        async def call_tool(self, tool_name, args):
            raise AssertionError("plugin must not run when modal is unwired")

    orc = MCPOrchestrator(acl=acl)  # no modal
    orc.register(
        _IrreversiblePlugin(),
        required_capabilities=frozenset({"external_data_write"}),
    )

    r = await orc.call_tool(
        "send", {"to": "x"},
        capability=Capability.EXTERNAL_DATA_WRITE,
    )
    assert r.is_error


@pytest.mark.asyncio
async def test_declared_irreversible_via_check_capabilities(acl):
    """Queue-path symmetry: check_capabilities ORs in the declaration
    too, so a queued candidate for a declared-irreversible tool routes
    through the modal even though the queue path passes only
    CallFlags(passive=True). This is the most production-relevant path
    after #139 because cerebral/main.py:1167-1168 is the only existing
    dispatch site that supplies a capability today."""
    prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )

    class _IrreversiblePlugin:
        name = "mail"

        def list_tools(self):
            return [Tool(
                name="send", description="d", plugin=self.name,
                irreversible=True,
            )]

        async def call_tool(self, tool_name, args):
            return ToolResult(content="sent")

    orc = MCPOrchestrator(acl=acl, modal=modal)
    orc.register(
        _IrreversiblePlugin(),
        required_capabilities=frozenset({"external_data_write"}),
    )

    # No flags passed — check_capabilities should still route through
    # the modal because the Tool declares irreversible.
    decision = await orc.check_capabilities(
        "send", frozenset({"external_data_write"}), None,
    )
    assert decision is Decision.SILENT
    assert len(prompt.received) == 1


@pytest.mark.asyncio
async def test_set_modal_surface_late_binding(acl):
    plugin = _StubPlugin()
    orc = MCPOrchestrator(acl=acl)
    orc.register(plugin, required_capabilities=frozenset({"fs_delete"}))

    # No modal yet — irreversible fails closed.
    r1 = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert r1.is_error

    # Wire the modal — irreversible now routes through it.
    prompt = _FakePrompt(CHOICE_ACCEPT)
    modal = ModalSurface(
        prompt_fn=prompt, has_subscriber_fn=lambda: True,
        request_id_fn=lambda: "m1",
    )
    orc.set_modal_surface(modal)
    assert orc.modal_surface is modal

    r2 = await orc.call_tool(
        "stub.act", {}, capability=Capability.FS_DELETE,
        flags=CallFlags(irreversible=True),
    )
    assert not r2.is_error
    assert plugin.calls == [("stub.act", {})]
