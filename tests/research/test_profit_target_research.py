from types import SimpleNamespace
import pandas as pd

from scripts.run_profit_target_research import one_trade


def candidate():
    return SimpleNamespace(ticker="TEST", candidate_id="c1", date=pd.Timestamp("2025-01-02"),
                           expiration=pd.Timestamp("2025-01-10"), short_strike=100.0,
                           long_strike=95.0, initial_credit=1.0, planned_loss=100.0,
                           theoretical_max_loss=400.0)


def marks(values, expiration=None):
    dates = pd.date_range("2025-01-03", periods=len(values), freq="D")
    return pd.DataFrame({"mark_date": dates, "spread_mark": values,
                         "quote_available": True,
                         "is_expiration": [False] * (len(values) - 1) + [bool(expiration)]})


def test_exact_40_50_70_target_equality_and_first_hit():
    for target, mark in ((.40, .60), (.50, .50), (.70, .30)):
        row = one_trade(candidate(), marks([.9, mark, .2]), target)
        assert row["exit_reason"] == "PROFIT_TARGET"
        assert row["exit_date"] == pd.Timestamp("2025-01-04")


def test_stop_before_target_and_target_before_stop():
    assert one_trade(candidate(), marks([2.0, .5]), .50)["exit_reason"] == "STOP"
    assert one_trade(candidate(), marks([.5, 2.0]), .50)["exit_reason"] == "PROFIT_TARGET"


def test_no_target_and_expiration_before_target():
    assert one_trade(candidate(), marks([.9, .8]), .40)["exit_reason"] == "DATA_END"
    assert one_trade(candidate(), marks([.8, .8], expiration=True), .40)["exit_reason"] == "EXPIRATION"


def test_no_lookahead_and_deterministic_rerun():
    m = marks([2.0, .5, .2])
    first = one_trade(candidate(), m, .50)
    second = one_trade(candidate(), m, .50)
    assert first == second
    assert first["exit_reason"] == "STOP"


def test_candidate_identities_are_independent_of_target():
    ids = {one_trade(candidate(), marks([.5]), target)["candidate_id"] for target in (.40, .50, .70)}
    assert ids == {"c1"}
