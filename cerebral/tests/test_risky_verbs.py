"""Verb-denylist tests for Issue #52.

The risk heuristic is a tokenised string match against a closed frozenset.
No stemming — "send" matches, "sends" / "sending" do not. The list is the
v1 vocabulary pinned in ADR-0005 / the #52 sharpener.
"""

from __future__ import annotations

import pytest

from cerebral.action_queue.risky_verbs import RISKY_VERBS, is_risky


# Pin the closed v1 vocabulary so a change here is a deliberate ADR-touching
# decision and not a drive-by edit.
EXPECTED_VOCABULARY = frozenset({
    "send", "transfer", "wire", "delete", "purchase",
    "pay", "unlock", "disable",
})


def test_vocabulary_is_pinned():
    assert RISKY_VERBS == EXPECTED_VOCABULARY


@pytest.mark.parametrize("verb", sorted(EXPECTED_VOCABULARY))
def test_each_verb_is_detected(verb):
    assert is_risky(f"please {verb} the report") is True


@pytest.mark.parametrize("verb", sorted(EXPECTED_VOCABULARY))
def test_verb_is_case_insensitive(verb):
    # Capitalised at sentence start — same word, must still match.
    assert is_risky(verb.capitalize() + " the file") is True
    assert is_risky(verb.upper() + " THE FILE") is True


def test_empty_string_is_not_risky():
    assert is_risky("") is False


def test_no_risky_verbs_is_not_risky():
    assert is_risky("Read my notes about the meeting") is False
    assert is_risky("Summarise this article") is False
    assert is_risky("Check the weather") is False


def test_no_stemming_inflected_forms_do_not_match():
    # "sends" / "sending" / "sent" must NOT match — sharpener #1 pins
    # simple token-match, no stemming.
    assert is_risky("sends a text to John") is False
    assert is_risky("sending a text to John") is False
    assert is_risky("sent a text to John") is False
    assert is_risky("deleted the file") is False
    assert is_risky("purchases a subscription") is False


def test_punctuation_does_not_block_match():
    # The verb is at a word boundary even with adjacent punctuation.
    assert is_risky("send, then forget.") is True
    assert is_risky("delete: the old logs") is True
    assert is_risky("Pay! the invoice") is True


def test_substring_inside_word_does_not_match():
    # "transferable" contains "transfer" but isn't tokenised as "transfer".
    # Token boundaries protect against false positives.
    assert is_risky("the funds are transferable") is False
    assert is_risky("pendulum motion") is False  # "pend" not a verb anyway


def test_multiple_risky_verbs_still_returns_true():
    assert is_risky("send and then delete") is True


def test_risky_verb_anywhere_in_string():
    assert is_risky("schedule a wire transfer for tomorrow") is True
    assert is_risky("ask John to disable the alarm at 9") is True
