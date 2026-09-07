from __future__ import annotations

from cerebral.verification import VerifyResult, verify_plugin_test_file

def test_verify_result_shape_and_defaults() -> None:
    # Assert shape
    result = VerifyResult(passed=True, evidence="test", score=0.5)
    assert result.passed is True
    assert result.evidence == "test"
    assert result.score == 0.5

    # Assert score defaults to None
    result_none_score = VerifyResult(passed=False, evidence="fail")
    assert result_none_score.score is None


def test_verify_plugin_test_file_existing_plugin() -> None:
    # clock.py has a real test file on disk (cerebral/tests/test_plugin_clock.py)
    result = verify_plugin_test_file("clock")
    assert result.passed is True


def test_verify_plugin_test_file_missing_plugin() -> None:
    result = verify_plugin_test_file("definitely_not_a_real_plugin_xyz")
    assert result.passed is False
    assert "test_plugin_definitely_not_a_real_plugin_xyz.py" in result.evidence
