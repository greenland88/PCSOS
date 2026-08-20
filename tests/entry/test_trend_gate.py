from types import SimpleNamespace

import pytest

from pcs.entry.trend_gate import evaluate_trend_gate


def fixture(state="A", health="healthy", direction="bullish", setup="shallow_pullback", score=80, available=True):
    score_result = SimpleNamespace(available=available, trend_state=state, trend_score=score, warnings=())
    interpretation = SimpleNamespace(available=available, trend_health=health, trend_direction=direction, setup_context=setup, warnings=())
    snapshot = SimpleNamespace(available=available)
    return score_result, interpretation, snapshot


def test_a_bullish_healthy_passes():
    assert evaluate_trend_gate(*fixture()).trend_gate_result == "PASS"


def test_b_healthy_pullback_passes():
    assert evaluate_trend_gate(*fixture(state="B", setup="healthy_pullback")).trend_gate_result == "PASS"


def test_c_is_watch():
    assert evaluate_trend_gate(*fixture(state="C", health="mixed")).trend_gate_result == "WATCH"


def test_d_neutral_is_watch():
    assert evaluate_trend_gate(*fixture(state="D", health="mixed", direction="neutral", setup="neutral")).trend_gate_result == "WATCH"


def test_d_bearish_is_reject():
    assert evaluate_trend_gate(*fixture(state="D", health="weakening", direction="bearish")).trend_gate_result == "REJECT"


def test_breakdown_is_reject():
    assert evaluate_trend_gate(*fixture(setup="breakdown")).trend_gate_result == "REJECT"


def test_broken_health_is_reject():
    assert evaluate_trend_gate(*fixture(health="broken")).trend_gate_result == "REJECT"


def test_e_is_reject():
    assert evaluate_trend_gate(*fixture(state="E", health="weakening")).trend_gate_result == "REJECT"


def test_high_score_bearish_is_reject():
    assert evaluate_trend_gate(*fixture(score=95, direction="bearish", health="healthy")).trend_gate_result == "REJECT"


def test_low_score_without_bearish_or_breakdown_is_not_mechanically_rejected():
    result = evaluate_trend_gate(*fixture(state="D", score=20, direction="neutral", health="mixed", setup="neutral"))
    assert result.trend_gate_result == "WATCH"


def test_unavailable_is_explicit():
    result = evaluate_trend_gate(*fixture(available=False))
    assert result.available is False
    assert result.trend_gate_result is None
    assert "trend_score_unavailable" in result.warnings


def test_input_objects_are_not_modified():
    values = fixture()
    before = [repr(value) for value in values]
    evaluate_trend_gate(*values)
    assert [repr(value) for value in values] == before
