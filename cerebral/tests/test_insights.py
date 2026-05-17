"""
InsightsEngine tests — Issue #13.

Unit tests use an in-memory SQLite DB; no disk writes, no external services.
Each test constructs a fresh InsightsEngine(:memory:) unless testing persistence.

Slices:
  1. record_signal stores signals; maybe_create_insight creates insight at threshold
  2. list_insights returns per-profile insights
  3. delete_insight removes it; False for unknown id
  4. pin_insight toggles pinned flag; False for unknown id
  5. edit_insight updates description; False for unknown id
  6. Per-profile isolation
  7. No duplicate insight created for same pattern key
  8. to_dict has required IPC fields
  9. Persistence across instances
"""
import pytest
from cerebral.insights.engine import Insight, InsightsEngine


# ── helpers ────────────────────────────────────────────────────────────────────

def _eng(profile_id: int = 1) -> InsightsEngine:
    eng = InsightsEngine(profile_id, db_path=":memory:")
    eng.PATTERN_THRESHOLD = 3
    return eng


def _fill_signals(eng: InsightsEngine, n: int, *, title="Set alarm", tool_name="set_alarm") -> None:
    for _ in range(n):
        eng.record_signal("approve", title, tool_name=tool_name)
        eng.maybe_create_insight(title, tool_name=tool_name)


# ── Slice 1: record_signal + maybe_create_insight ─────────────────────────────

def test_no_insight_below_threshold():
    eng = _eng()
    _fill_signals(eng, 2)
    assert eng.list_insights() == []


def test_insight_created_at_threshold():
    eng = _eng()
    _fill_signals(eng, 3)
    insights = eng.list_insights()
    assert len(insights) == 1


def test_maybe_create_insight_returns_insight_at_threshold():
    eng = _eng()
    for i in range(3):
        eng.record_signal("approve", "Set alarm", tool_name="set_alarm")
        result = eng.maybe_create_insight("Set alarm", tool_name="set_alarm")
        if i < 2:
            assert result is None
        else:
            assert isinstance(result, Insight)


def test_insight_has_expected_fields():
    eng = _eng()
    _fill_signals(eng, 3)
    insight = eng.list_insights()[0]
    assert insight.id
    assert insight.profile_id == 1
    assert insight.description
    assert insight.example
    assert isinstance(insight.pinned, bool)
    assert insight.created_at
    assert insight.updated_at


def test_insight_example_reflects_trigger():
    eng = _eng()
    _fill_signals(eng, 3, title="Set alarm", tool_name="set_alarm")
    insight = eng.list_insights()[0]
    assert "set_alarm" in insight.example or "alarm" in insight.example.lower()


def test_dismiss_signals_also_recorded():
    eng = _eng()
    for _ in range(3):
        eng.record_signal("dismiss", "Play music", tool_name="play_music")
        eng.maybe_create_insight("Play music", tool_name="play_music")
    assert len(eng.list_insights()) == 1


# ── Slice 2: list_insights ────────────────────────────────────────────────────

def test_list_insights_empty_initially():
    eng = _eng()
    assert eng.list_insights() == []


def test_list_insights_returns_all_for_profile():
    eng = _eng()
    _fill_signals(eng, 3, title="Set alarm", tool_name="set_alarm")
    _fill_signals(eng, 3, title="Play music", tool_name="play_music")
    assert len(eng.list_insights()) == 2


# ── Slice 3: delete_insight ───────────────────────────────────────────────────

def test_delete_insight_removes_it():
    eng = _eng()
    _fill_signals(eng, 3)
    insight_id = eng.list_insights()[0].id
    result = eng.delete_insight(insight_id)
    assert result is True
    assert eng.list_insights() == []


def test_delete_insight_unknown_id_returns_false():
    eng = _eng()
    assert eng.delete_insight("no-such-id") is False


def test_delete_insight_does_not_affect_others():
    eng = _eng()
    _fill_signals(eng, 3, title="Set alarm", tool_name="set_alarm")
    _fill_signals(eng, 3, title="Play music", tool_name="play_music")
    ids = [i.id for i in eng.list_insights()]
    eng.delete_insight(ids[0])
    remaining = eng.list_insights()
    assert len(remaining) == 1
    assert remaining[0].id == ids[1]


# ── Slice 4: pin_insight ──────────────────────────────────────────────────────

def test_pin_insight_sets_pinned_true():
    eng = _eng()
    _fill_signals(eng, 3)
    insight_id = eng.list_insights()[0].id
    result = eng.pin_insight(insight_id)
    assert result is True
    assert eng.list_insights()[0].pinned is True


def test_pin_insight_toggles_off():
    eng = _eng()
    _fill_signals(eng, 3)
    insight_id = eng.list_insights()[0].id
    eng.pin_insight(insight_id)
    eng.pin_insight(insight_id)
    assert eng.list_insights()[0].pinned is False


def test_pin_insight_unknown_id_returns_false():
    eng = _eng()
    assert eng.pin_insight("no-such-id") is False


# ── Slice 5: edit_insight ─────────────────────────────────────────────────────

def test_edit_insight_updates_description():
    eng = _eng()
    _fill_signals(eng, 3)
    insight_id = eng.list_insights()[0].id
    result = eng.edit_insight(insight_id, "Custom description")
    assert result is True
    assert eng.list_insights()[0].description == "Custom description"


def test_edit_insight_unknown_id_returns_false():
    eng = _eng()
    assert eng.edit_insight("no-such-id", "whatever") is False


def test_edit_insight_updates_updated_at():
    eng = _eng()
    _fill_signals(eng, 3)
    insight = eng.list_insights()[0]
    original_updated_at = insight.updated_at
    eng.edit_insight(insight.id, "New description")
    updated = eng.list_insights()[0]
    assert updated.updated_at >= original_updated_at


# ── Slice 6: per-profile isolation ───────────────────────────────────────────

def test_insights_isolated_per_profile(tmp_path):
    db = tmp_path / "test.db"
    eng1 = InsightsEngine(1, db_path=str(db))
    eng2 = InsightsEngine(2, db_path=str(db))
    eng1.PATTERN_THRESHOLD = 3
    eng2.PATTERN_THRESHOLD = 3

    _fill_signals(eng1, 3, title="Set alarm", tool_name="set_alarm")
    assert eng2.list_insights() == []


def test_profile1_insights_dont_bleed_to_profile2(tmp_path):
    db = tmp_path / "test.db"
    eng1 = InsightsEngine(1, db_path=str(db))
    eng2 = InsightsEngine(2, db_path=str(db))
    eng1.PATTERN_THRESHOLD = 3
    eng2.PATTERN_THRESHOLD = 3

    _fill_signals(eng1, 3, title="A", tool_name="tool_a")
    _fill_signals(eng2, 3, title="B", tool_name="tool_b")

    assert len(eng1.list_insights()) == 1
    assert len(eng2.list_insights()) == 1
    assert eng1.list_insights()[0].example != eng2.list_insights()[0].example


# ── Slice 7: no duplicate insight for same pattern ───────────────────────────

def test_no_duplicate_insight_for_same_pattern():
    eng = _eng()
    for _ in range(6):
        eng.record_signal("approve", "Set alarm", tool_name="set_alarm")
        eng.maybe_create_insight("Set alarm", tool_name="set_alarm")
    assert len(eng.list_insights()) == 1


def test_pattern_key_uses_tool_name_over_title():
    eng = _eng()
    _fill_signals(eng, 3, title="Different title each time", tool_name="set_alarm")
    assert len(eng.list_insights()) == 1


def test_pattern_key_falls_back_to_normalised_title():
    eng = _eng()
    for _ in range(3):
        eng.record_signal("approve", "  Set Alarm  ", tool_name=None)
        eng.maybe_create_insight("  Set Alarm  ", tool_name=None)
    assert len(eng.list_insights()) == 1


# ── Slice 8: to_dict IPC serialisation ───────────────────────────────────────

def test_to_dict_has_required_fields():
    eng = _eng()
    _fill_signals(eng, 3)
    d = eng.list_insights()[0].to_dict()
    for key in ("id", "profile_id", "description", "example", "pinned", "created_at", "updated_at"):
        assert key in d, f"missing key: {key}"


def test_to_dict_pinned_is_bool():
    eng = _eng()
    _fill_signals(eng, 3)
    d = eng.list_insights()[0].to_dict()
    assert isinstance(d["pinned"], bool)


# ── Slice 9: persistence across instances ────────────────────────────────────

def test_insights_persist_across_instances(tmp_path):
    db = tmp_path / "test.db"
    eng1 = InsightsEngine(1, db_path=str(db))
    eng1.PATTERN_THRESHOLD = 3
    _fill_signals(eng1, 3)
    insight_id = eng1.list_insights()[0].id

    eng2 = InsightsEngine(1, db_path=str(db))
    assert any(i.id == insight_id for i in eng2.list_insights())


def test_pin_persists_across_instances(tmp_path):
    db = tmp_path / "test.db"
    eng1 = InsightsEngine(1, db_path=str(db))
    eng1.PATTERN_THRESHOLD = 3
    _fill_signals(eng1, 3)
    iid = eng1.list_insights()[0].id
    eng1.pin_insight(iid)

    eng2 = InsightsEngine(1, db_path=str(db))
    assert eng2.list_insights()[0].pinned is True


# ── Slice 10: list_insights ordering — pinned-first (Issue #88) ───────────────


def _with_monotonic_clock(eng: InsightsEngine) -> None:
    """Force strictly-increasing created_at so ordering assertions are
    deterministic regardless of wall-clock microsecond collisions."""
    import itertools

    counter = itertools.count()
    eng._now = lambda: f"2026-01-01T00:00:00.{next(counter):06d}+00:00"


def _make(eng: InsightsEngine, example: str) -> str:
    _fill_signals(eng, 3, title=example, tool_name=example)
    return next(i.id for i in eng.list_insights() if i.example == example)


def test_list_insights_pinned_floats_to_top():
    eng = _eng()
    _with_monotonic_clock(eng)
    _make(eng, "a")
    _make(eng, "b")
    c_id = _make(eng, "c")
    assert [i.example for i in eng.list_insights()] == ["a", "b", "c"]

    eng.pin_insight(c_id)
    ins = eng.list_insights()
    assert ins[0].id == c_id and ins[0].pinned is True
    assert [i.example for i in ins[1:]] == ["a", "b"]


def test_list_insights_two_pinned_stay_created_asc():
    eng = _eng()
    _with_monotonic_clock(eng)
    a_id = _make(eng, "a")
    _make(eng, "b")
    c_id = _make(eng, "c")
    eng.pin_insight(c_id)
    eng.pin_insight(a_id)
    assert [i.example for i in eng.list_insights()] == ["a", "c", "b"]


def test_list_insights_deterministic_across_calls():
    eng = _eng()
    for n in range(8):
        _make(eng, f"t{n}")
    assert [i.id for i in eng.list_insights()] == [i.id for i in eng.list_insights()]


# ── Slice 11: edit_insight blank guard (Issue #88) ────────────────────────────


def test_edit_insight_blank_returns_false():
    eng = _eng()
    _fill_signals(eng, 3)
    insight = eng.list_insights()[0]
    assert eng.edit_insight(insight.id, "") is False
    assert eng.list_insights()[0].description == insight.description


def test_edit_insight_whitespace_returns_false_no_updated_at_bump():
    eng = _eng()
    _fill_signals(eng, 3)
    insight = eng.list_insights()[0]
    assert eng.edit_insight(insight.id, "   \t\n ") is False
    after = eng.list_insights()[0]
    assert after.description == insight.description
    assert after.updated_at == insight.updated_at
