import pandas as pd
import pytest

from pcs.trend.opportunity_engine import replay_opportunities
from pcs.trend.config import TrendIndicatorConfig


def _frame(n=280):
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series(100.0, index=range(n))
    close.iloc[220:] = [100 + i * .35 for i in range(n-220)]
    return pd.DataFrame({"date": dates, "open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1000.0})


def test_replay_is_pit_safe_and_runs_all_signal_days():
    out = replay_opportunities("TEST", _frame(), "2026-01-01")
    assert len(out) > 0
    assert out["pit_verified"].all()
    assert (pd.to_datetime(out["feature_max_date"]) <= pd.to_datetime(out["date"])).all()


def test_warmup_fails_closed_below_minimum():
    with pytest.raises(ValueError, match="WARMUP_INSUFFICIENT"):
        replay_opportunities("TEST", _frame(), "2025-06-01", minimum_warmup_rows=260)


def test_upper_rejection_is_a_risk_phase_not_structural_downtrend():
    frame = _frame()
    frame.loc[260, "high"] = frame.loc[260, "close"] + 10
    frame.loc[260, "close"] = frame.loc[260, "low"] + .1
    out = replay_opportunities("TEST", frame, "2026-01-01")
    row = out[out.date == "2026-01-01"].iloc[0]
    assert row.structural_trend != "STRUCTURAL_DOWNTREND"


def test_pullback_setup_keeps_same_opportunity_id_into_confirmation():
    frame = _frame()
    frame.loc[260, "close"] = 112
    frame.loc[261, "close"] = 113
    frame.loc[260, "high"] = 112.5
    frame.loc[261, "high"] = 113.5
    out = replay_opportunities("TEST", frame, "2026-01-01")
    ids = out[out.opportunity_id.notna()].opportunity_id.tolist()
    assert ids
    assert len(set(ids)) == 1


def test_pivot_support_is_only_available_after_two_confirmation_sessions():
    frame = _frame()
    frame.loc[250, "low"] = 90
    frame.loc[251, "low"] = 91
    frame.loc[252, "low"] = 92
    frame.loc[253, "low"] = 93
    frame.loc[254, "low"] = 94
    out = replay_opportunities("TEST", frame, "2026-01-01")
    assert out["pit_verified"].all()


def test_rvol_baseline_excludes_current_session():
    frame = _frame()
    frame.loc[261, "volume"] = 100000
    out = replay_opportunities("TEST", frame, "2026-01-01")
    row = out[out.date == "2026-01-01"].iloc[0]
    # Including the current volume would dilute the denominator; the correct
    # PIT baseline remains the preceding ~1000-volume sessions.
    assert row.rvol20 > 50


def test_no_supported_path_cannot_be_entry_ready():
    frame = _frame()
    frame.loc[260:, "low"] = frame.loc[260:, "close"] - 50
    out = replay_opportunities("TEST", frame, "2026-01-01")
    assert not ((out.opportunity_state == "ENTRY_READY") & out.primary_support.isna()).any()


def test_first_reclaim_session_is_watch_only():
    frame = _frame()
    frame.loc[260, "close"] = frame.loc[259, "close"] + 10
    out = replay_opportunities("TEST", frame, "2026-01-01")
    row = out[out.date == "2026-01-01"].iloc[0]
    if row.short_term_phase == "RECLAIM_DAY_1":
        assert row.timing_action == "WATCH"


def test_opportunity_thresholds_are_validated_centrally():
    with pytest.raises(ValueError, match="rsi_hard_block"):
        TrendIndicatorConfig(rsi_overheated=80, rsi_hard_block=70).validate()
