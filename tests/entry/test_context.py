from types import SimpleNamespace

from pcs.entry.context import build_entry_context


def gates(trend="PASS", pullback="PASS", strike="PASS", available=True):
    return (
        SimpleNamespace(available=available, trend_gate_result=trend, warnings=()),
        SimpleNamespace(available=available, pullback_gate_result=pullback, warnings=()),
        SimpleNamespace(available=available, strike_gate_result=strike, warnings=()),
    )


def test_all_pass_is_ready():
    result = build_entry_context(*gates())
    assert result.entry_context_state == "READY"
    assert set(result.positive_gates) == {"trend_gate", "pullback_gate", "strike_gate"}


def test_pullback_wait_is_wait():
    result = build_entry_context(*gates(pullback="WAIT"))
    assert result.entry_context_state == "WAIT"
    assert result.blocking_gates == ("pullback_gate",)


def test_strike_marginal_is_wait():
    result = build_entry_context(*gates(strike="MARGINAL"))
    assert result.entry_context_state == "WAIT"
    assert result.blocking_gates == ("strike_gate",)


def test_trend_watch_is_wait():
    result = build_entry_context(*gates(trend="WATCH"))
    assert result.entry_context_state == "WAIT"


def test_any_reject_is_reject():
    result = build_entry_context(*gates(trend="REJECT", pullback="PASS", strike="PASS"))
    assert result.entry_context_state == "REJECT"
    assert result.blocking_gates == ("trend_gate",)


def test_later_reject_cannot_be_overridden():
    result = build_entry_context(*gates(trend="REJECT", pullback="WAIT", strike="PASS"))
    assert result.entry_context_state == "REJECT"
    assert "trend_gate" in result.blocking_gates
    assert "pullback_gate" not in result.blocking_gates


def test_unavailable_is_explicit():
    result = build_entry_context(*gates(available=False))
    assert result.available is False
    assert result.entry_context_state is None
    assert "trend_gate_unavailable" in result.warnings


def test_inputs_are_not_modified():
    values = gates()
    before = [repr(value) for value in values]
    build_entry_context(*values)
    assert [repr(value) for value in values] == before


def test_ready_is_not_a_trade_action():
    result = build_entry_context(*gates())
    assert result.entry_context_state == "READY"
    assert not hasattr(result, "sell")
    assert not hasattr(result, "wait")
