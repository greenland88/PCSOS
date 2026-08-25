import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def test_amd_transition_report_is_descriptive_and_true_runs():
    summary = json.loads((ROOT / "research_outputs/amd_early_recovery_new_entry/state_transition_report/summary.json").read_text())
    episodes = pd.read_csv(ROOT / "research_outputs/amd_early_recovery_new_entry/state_transition_report/breakdown_runs.csv")
    assert summary["data_source"] == "PCS_CANONICAL_DATA"
    assert summary["population_semantics"] == "MAXIMAL_CONTIGUOUS_BREAKDOWN_RUNS"
    assert summary["true_breakdown_run_count"] == len(episodes) == 74
    assert summary["signal_execution"] == "NOT_RUN"
    assert summary["final_oos_read"] is False
    assert summary["run_end_invariant_breakdown_after_1d_count"] == 0
    assert {"breakdown_start", "breakdown_end", "breakdown_duration", "prior_state",
            "state_after_1d", "state_after_3d", "state_after_5d", "state_after_10d",
            "first_stabilizing_date", "first_pullback_in_uptrend_date", "first_uptrend_date",
            "prior_support_reclaimed_date", "censored", "status"}.issubset(episodes.columns)


def test_pit_timeline_cache_has_identity_contract():
    frame = pd.read_parquet(ROOT / "research_outputs/amd_early_recovery_new_entry/pit_state_timeline.parquet")
    assert {"ticker", "data_version", "code_version", "feature_config_hash",
            "date_range_start", "date_range_end", "created_at"}.issubset(frame.columns)
    assert set(frame.ticker) == {"AMD"}
