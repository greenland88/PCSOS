import importlib.util
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "report_amd_breakdown_state_transitions.py"
spec = importlib.util.spec_from_file_location("breakdown_report", MODULE_PATH)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def _timeline(states):
    dates = pd.bdate_range("2024-01-02", periods=len(states))
    return pd.DataFrame({"date": dates, "final_underlying_state": states})


def test_twelve_consecutive_breakdown_days_are_one_run():
    frame, summary = report.build_report(_timeline(["BREAKDOWN"] * 12 + ["DOWNTREND"] * 10))
    assert len(frame) == 1
    assert frame.iloc[0].breakdown_duration == 12


def test_run_end_next_trading_day_cannot_be_breakdown():
    frame, summary = report.build_report(_timeline(["BREAKDOWN"] * 4 + ["DOWNTREND"] * 10))
    assert frame.iloc[0].state_after_1d == "DOWNTREND"
    assert summary["run_end_invariant_breakdown_after_1d_count"] == 0


def test_non_breakdown_day_creates_two_runs():
    frame, _ = report.build_report(_timeline(["BREAKDOWN"] * 3 + ["DOWNTREND"] + ["BREAKDOWN"] * 3 + ["UPTREND"] * 10))
    assert len(frame) == 2
    assert list(frame.breakdown_duration) == [3, 3]


def test_transitions_are_anchored_at_run_end():
    frame, summary = report.build_report(_timeline(["BREAKDOWN", "BREAKDOWN", "STABILIZING", "UPTREND"] + ["DOWNTREND"] * 10))
    assert frame.iloc[0].breakdown_duration == 2
    assert frame.iloc[0].state_after_1d == "STABILIZING"
    assert summary["transition_counts_anchored_at_run_end"]["state_after_1d"]["STABILIZING"] == 1


def test_recovery_candidate_counts_are_independent():
    states = ["BREAKDOWN"] * 2 + ["STABILIZING"] + ["DOWNTREND"] * 2 + ["BREAKDOWN"] * 2 + ["PULLBACK_IN_UPTREND"] + ["DOWNTREND"] * 10
    frame, summary = report.build_report(_timeline(states))
    counts = summary["recovery_signal_counts"]
    assert counts["stabilizing"]["actual_signal_count"] == 1
    assert counts["pullback_in_uptrend"]["actual_signal_count"] == 1
    assert counts["uptrend"]["actual_signal_count"] == 0


def test_support_missing_is_not_zero_reclaim():
    frame, summary = report.build_report(_timeline(["BREAKDOWN"] * 2 + ["DOWNTREND"] * 10))
    support = summary["support_availability_funnel"]
    assert support["status"] == "PIT_FEATURE_MISSING"
    assert support["classification"] == "SUPPORT_FEATURE_UNAVAILABLE"
    assert support["missing_feature_count"] == 1
    assert support["actual_signal_count"] == 0


def test_long_run_is_not_repeated_by_five_day_windows():
    frame, summary = report.build_report(_timeline(["BREAKDOWN"] * 20 + ["UPTREND"] * 10))
    assert len(frame) == 1
    assert frame.iloc[0].breakdown_duration == 20
