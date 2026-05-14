"""
Capability label-table tests — Issue #48.

The closed 16-class vocabulary is in `gate.Capability`. The tray and the
Permissions UI (#53) need short noun-phrase labels + one-sentence
descriptions for every class. These tests pin the completeness of both
tables and a few quality invariants (non-empty, ends without trailing
whitespace, mentions Felix in descriptions for tone consistency).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.security import (
    CAPABILITY_DESCRIPTION,
    CAPABILITY_LABEL,
    Capability,
    description_for,
    label_for,
)


# ---------------------------------------------------------------------------
# Slice 1 — exhaustiveness against the closed vocabulary
# ---------------------------------------------------------------------------


def test_every_capability_has_a_label():
    missing = set(Capability) - set(CAPABILITY_LABEL)
    assert missing == set(), f"missing labels for: {sorted(c.value for c in missing)}"


def test_every_capability_has_a_description():
    missing = set(Capability) - set(CAPABILITY_DESCRIPTION)
    assert missing == set(), f"missing descriptions for: {sorted(c.value for c in missing)}"


def test_label_table_has_no_extra_keys():
    extras = set(CAPABILITY_LABEL) - set(Capability)
    assert extras == set(), "label table holds keys outside the closed vocabulary"


def test_description_table_has_no_extra_keys():
    extras = set(CAPABILITY_DESCRIPTION) - set(Capability)
    assert extras == set()


def test_label_table_is_immutable():
    # MappingProxyType should reject mutation — protects against accidental
    # writes from later code.
    with pytest.raises(TypeError):
        CAPABILITY_LABEL[Capability.FS_READ] = "new"  # type: ignore[index]


def test_description_table_is_immutable():
    with pytest.raises(TypeError):
        CAPABILITY_DESCRIPTION[Capability.FS_READ] = "new"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Slice 2 — accessor helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cap", list(Capability))
def test_label_for_returns_string(cap):
    value = label_for(cap)
    assert isinstance(value, str)
    assert value


@pytest.mark.parametrize("cap", list(Capability))
def test_description_for_returns_string(cap):
    value = description_for(cap)
    assert isinstance(value, str)
    assert value


def test_label_for_matches_table():
    assert label_for(Capability.FS_WRITE) == CAPABILITY_LABEL[Capability.FS_WRITE]


def test_description_for_matches_table():
    assert description_for(Capability.SHELL_EXEC) == CAPABILITY_DESCRIPTION[Capability.SHELL_EXEC]


# ---------------------------------------------------------------------------
# Slice 3 — quality / tone invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cap", list(Capability))
def test_labels_have_no_trailing_punctuation(cap):
    label = CAPABILITY_LABEL[cap]
    assert not label.endswith("."), f"label for {cap.value} ends with a period"
    assert not label.endswith(" "), f"label for {cap.value} has trailing space"


@pytest.mark.parametrize("cap", list(Capability))
def test_descriptions_are_complete_sentences(cap):
    desc = CAPABILITY_DESCRIPTION[cap]
    assert desc.endswith("."), f"description for {cap.value} should end with a period"
    assert desc[0].isupper(), f"description for {cap.value} should start uppercase"


@pytest.mark.parametrize("cap", list(Capability))
def test_descriptions_mention_felix(cap):
    # Consistency: every description names the actor as "Felix" so the
    # tone is uniform across the prompt UI. Catches mid-implementation
    # drift if someone copy-pastes "the assistant" or "AI".
    assert "Felix" in CAPABILITY_DESCRIPTION[cap], (
        f"description for {cap.value} doesn't mention Felix: "
        f"{CAPABILITY_DESCRIPTION[cap]!r}"
    )


@pytest.mark.parametrize("cap", list(Capability))
def test_labels_under_60_chars(cap):
    # Notification UI envelope: keep labels short so the tray prompt
    # title doesn't wrap awkwardly on narrow screens.
    assert len(CAPABILITY_LABEL[cap]) <= 60


@pytest.mark.parametrize("cap", list(Capability))
def test_descriptions_under_240_chars(cap):
    # The Why? expander has a fixed width; long descriptions break the
    # layout. Tune the bound if a class genuinely needs more space.
    assert len(CAPABILITY_DESCRIPTION[cap]) <= 240
