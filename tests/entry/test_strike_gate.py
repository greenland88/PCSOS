from types import SimpleNamespace

from pcs.entry.strike_gate import evaluate_short_strike


def fixture(strike=190, gate="PASS", pullback_gate="PASS", support=216, support_state="strong", available=True):
    snapshot = SimpleNamespace(available=available, current_close=225.16, current_atr=6.9264, support=SimpleNamespace(nearest_support=support, support_confluence_state=support_state, current_atr=6.9264))
    interpretation = SimpleNamespace(available=available)
    trend = SimpleNamespace(available=available, trend_gate_result=gate, warnings=())
    pullback = SimpleNamespace(available=available, pullback_gate_result=pullback_gate, warnings=())
    return strike, snapshot, interpretation, trend, pullback


def test_far_strike_passes():
    assert evaluate_short_strike(*fixture(190)).strike_gate_result == "PASS"


def test_three_day_enough_five_day_marginal():
    assert evaluate_short_strike(*fixture(211)).strike_gate_result == "MARGINAL"


def test_strike_too_close_rejects():
    assert evaluate_short_strike(*fixture(220)).strike_gate_result == "REJECT"


def test_strike_above_support_rejects():
    result = evaluate_short_strike(*fixture(220, support=216, support_state="strong"))
    assert result.strike_gate_result == "REJECT"


def test_below_strong_support_is_positive():
    result = evaluate_short_strike(*fixture(200, support=216, support_state="strong"))
    assert "strike_below_support_buffer" in result.reasons


def test_weak_support_is_not_hard_protection():
    result = evaluate_short_strike(*fixture(211, support=216, support_state="weak"))
    assert result.strike_gate_result in {"MARGINAL", "PASS"}


def test_high_vol_atr_scaling():
    result = evaluate_short_strike(*fixture(190))
    assert result.normal_move_buffer_3d_atr == 1.7320508075688772
    assert result.normal_move_buffer_5d_atr == 2.23606797749979


def test_trend_reject_rejects():
    assert evaluate_short_strike(*fixture(gate="REJECT")).strike_gate_result == "REJECT"


def test_pullback_reject_rejects():
    assert evaluate_short_strike(*fixture(pullback_gate="REJECT")).strike_gate_result == "REJECT"


def test_pullback_wait_can_still_evaluate_but_not_upgrade_context():
    result = evaluate_short_strike(*fixture(190, pullback_gate="WAIT"))
    assert result.strike_gate_result == "PASS"
    assert "pullback_gate_wait" in result.reasons


def test_config_changes_result():
    from dataclasses import replace
    from pcs.trend import TrendIndicatorConfig
    values = fixture(211)
    default = evaluate_short_strike(*values)
    strict = evaluate_short_strike(*values, config=replace(TrendIndicatorConfig(), strike_min_buffer_atr_pass=4.0, strike_min_buffer_atr_marginal=3.0))
    assert default.strike_gate_result != strict.strike_gate_result


def test_unavailable_is_explicit():
    result = evaluate_short_strike(*fixture(available=False))
    assert result.available is False
    assert result.strike_gate_result is None


def test_inputs_are_not_modified():
    values = fixture()
    before = [repr(value) for value in values]
    evaluate_short_strike(*values)
    assert [repr(value) for value in values] == before
