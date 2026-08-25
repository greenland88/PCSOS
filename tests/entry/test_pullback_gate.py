from types import SimpleNamespace

from pcs.entry.pullback_gate import evaluate_pullback_gate


def fixture(gate="PASS", state="A", health="healthy", direction="bullish", pullback="healthy_pullback", support="strong", available=True, market="bullish"):
    trend_gate = SimpleNamespace(available=available, trend_gate_result=gate, warnings=())
    snapshot = SimpleNamespace(
        available=available,
        pullback=SimpleNamespace(available=available, pullback_state=pullback),
        support=SimpleNamespace(available=available, support_confluence_state=support),
        market_structure=SimpleNamespace(available=available, structure_state=market),
    )
    interpretation = SimpleNamespace(available=available, trend_health=health, trend_direction=direction, setup_context=pullback if pullback != "extended_uptrend" else "extended", warnings=())
    return trend_gate, snapshot, interpretation


def test_healthy_pullback_strong_support_passes():
    assert evaluate_pullback_gate(*fixture()).pullback_gate_result == "PASS"


def test_healthy_pullback_moderate_support_passes():
    assert evaluate_pullback_gate(*fixture(support="moderate")).pullback_gate_result == "PASS"


def test_healthy_pullback_weak_support_waits():
    assert evaluate_pullback_gate(*fixture(support="weak")).pullback_gate_result == "WAIT"


def test_shallow_pullback_waits():
    assert evaluate_pullback_gate(*fixture(pullback="shallow_pullback", support="strong")).pullback_gate_result == "WAIT"


def test_no_pullback_waits():
    assert evaluate_pullback_gate(*fixture(pullback="no_pullback", support="strong")).pullback_gate_result == "WAIT"


def test_extended_waits():
    assert evaluate_pullback_gate(*fixture(pullback="extended_uptrend", support="strong")).pullback_gate_result == "WAIT"


def test_unstable_healthy_bullish_waits():
    assert evaluate_pullback_gate(*fixture(pullback="unstable_pullback", support="moderate")).pullback_gate_result == "WAIT"


def test_unstable_weakening_rejects():
    assert evaluate_pullback_gate(*fixture(pullback="unstable_pullback", health="weakening")).pullback_gate_result == "REJECT"


def test_breakdown_rejects():
    assert evaluate_pullback_gate(*fixture(pullback="breakdown")).pullback_gate_result == "REJECT"


def test_trend_gate_reject_rejects():
    assert evaluate_pullback_gate(*fixture(gate="REJECT")).pullback_gate_result == "REJECT"


def test_trend_gate_watch_cannot_pass():
    assert evaluate_pullback_gate(*fixture(gate="WATCH")).pullback_gate_result == "WAIT"


def test_a_state_does_not_automatically_pass():
    assert evaluate_pullback_gate(*fixture(pullback="shallow_pullback", support="weak")).pullback_gate_result == "WAIT"


def test_b_without_support_does_not_pass():
    assert evaluate_pullback_gate(*fixture(state="B", support="none")).pullback_gate_result == "WAIT"


def test_unavailable_is_explicit():
    result = evaluate_pullback_gate(*fixture(available=False))
    assert result.available is False
    assert result.pullback_gate_result is None


def test_inputs_are_not_modified():
    values = fixture()
    before = [repr(value) for value in values]
    evaluate_pullback_gate(*values)
    assert [repr(value) for value in values] == before
