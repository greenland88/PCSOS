"""Full clean AMD scan for pre-existing frozen QQQ recovery definitions.

No AMD-specific signal or threshold is introduced here. Signal dates are
derived from the frozen QQQ definitions and then passed to the canonical PCS
replay engine.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "research_outputs" / "amd_canonical_test_dataset" / "amd_clean_testable_days.parquet"
OUT = ROOT / "research_outputs" / "amd_full_clean_frozen_transfer"

STRATEGIES = {
    "QQQ_CONTROLLED_RESET": {
        "name": "Controlled Reset",
        "origin": "QQQ entry-discovery / frozen historical-test family",
        "artifact": "research_outputs/qqq_frozen_historical_test_2024_2025/frozen_historical_test_spec.json",
        "version": "CONTROLLED_RESET; frozen definition used in QQQ historical test",
        "definition": "drawdown60 <= -0.02 AND ret10 > 0; first qualification per independent episode (>4 calendar days)",
        "amd_created": False, "amd_tuned": False,
    },
    "H006_RECOVERY_STABILIZATION": {
        "name": "Recovery Stabilization After Reset",
        "origin": "QQQ H006 entry-discovery / frozen historical-test family",
        "artifact": "research_outputs/qqq_frozen_historical_test_2024_2025/frozen_historical_test_spec.json",
        "version": "H006; frozen definition used in QQQ historical test",
        "definition": "drawdown60 <= -0.02 AND ret10 > 0 AND ret5 > 0; first recovery confirmation per independent episode (>4 calendar days)",
        "amd_created": False, "amd_tuned": False,
    },
    "H016_SMA50_RECLAIM": {
        "name": "SMA50 Reclaim After Weakness",
        "origin": "QQQ H016 entry-discovery / frozen historical-test family",
        "artifact": "research_outputs/qqq_frozen_historical_test_2024_2025/frozen_historical_test_spec.json",
        "version": "H016; frozen definition used in QQQ historical test",
        "definition": "drawdown60 <= -0.02 AND prior close_sma50_atr <= 0 AND current close_sma50_atr > 0; first reclaim per independent episode (>4 calendar days)",
        "amd_created": False, "amd_tuned": False,
    },
}


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    x = pd.read_parquet(IN).sort_values("date").reset_index(drop=True)
    x["date"] = pd.to_datetime(x.date)
    # The clean table is the complete starting population. This is a derived
    # field from canonical clean close/SMA50/ATR columns, not a new threshold.
    x["close_sma50_atr"] = (x.close - x.sma50) / x.atr14
    x["prior_close_sma50_atr"] = x.close_sma50_atr.shift(1)
    x["prior_date"] = x.date.shift(1)
    assert x.date.min() >= pd.Timestamp("2020-01-02") and x.date.max() <= pd.Timestamp("2026-08-18")
    assert len(x) == 1299 and x.testable_day.all()
    records = {}
    for sid, meta in STRATEGIES.items():
        if sid == "QQQ_CONTROLLED_RESET":
            condition = x.drawdown60.le(-0.02) & x.ret10.gt(0)
        elif sid == "H006_RECOVERY_STABILIZATION":
            condition = x.drawdown60.le(-0.02) & x.ret10.gt(0) & x.ret5.gt(0)
        else:
            condition = x.drawdown60.le(-0.02) & x.prior_close_sma50_atr.le(0) & x.close_sma50_atr.gt(0)
        q = x.loc[condition].copy()
        q["strategy_condition_true"] = True
        q["strategy_id"] = sid
        q["independent_episode_start"] = q.date.diff().dt.days.gt(4) | q.date.diff().isna()
        q["episode_number"] = q.independent_episode_start.cumsum()
        signals = q[q.independent_episode_start].copy()
        q.to_csv(OUT / f"{sid.lower()}_qualifying_days.csv", index=False)
        signals[["date", "strategy_id", "episode_number"]].to_csv(OUT / f"{sid.lower()}_signal_dates.csv", index=False)
        records[sid] = {"metadata": meta, "qualifying_days": int(len(q)), "independent_episodes": int(q.episode_number.nunique()), "signal_dates": signals.date.dt.strftime("%Y-%m-%d").tolist(), "qualifying_days_by_year": q.date.dt.year.value_counts().sort_index().astype(int).to_dict(), "signal_dates_by_year": signals.date.dt.year.value_counts().sort_index().astype(int).to_dict()}
        spec = {"research_id": f"amd_full_clean_{sid.lower()}", "ticker": "AMD", "research_mode": "CURRENT_STRATEGY_REPLAY", "hypothesis": f"Cross-ticker transfer test of frozen {meta['name']}", "population_source": {"type": "ticker_daily_calendar", "authoritative_clean_dataset": str(IN.relative_to(ROOT)), "frozen": False, "point_in_time": True}, "signal_definition": {"strategy_id": sid, "definition_artifact": meta["artifact"], "creates_new_entry_dates": True, "execution_dates": records[sid]["signal_dates"], "track_a_execution_only": True}, "entry_date_rule": {"rule": "frozen_signal_date_first_per_independent_episode"}, "date_range": {"start": "2020-01-02", "end": "2026-08-18", "split": "FULL_CLEAN_AVAILABLE_HISTORY"}, "split_policy": {"name": "FULL_CLEAN_AVAILABLE_HISTORY", "train_end": "2026-08-18", "2026_included": True}, "contract_selection_policy": {"mode": "AUTHORITATIVE_CURRENT_STRATEGY_SELECTOR", "width_priority": [5, 10, 2], "as_of_only": True}, "lifecycle_policy": {"source": "canonical_lifecycle_adapter", "no_future_selection": True}, "frozen_parameters": {}, "allowed_parameters": {"research_only": True}, "final_oos_access": False, "production_changes_allowed": False}
        (OUT / f"{sid}.yaml").write_text("\n".join(f"{k}: {json.dumps(v, default=str)}" for k, v in spec.items()) + "\n", encoding="utf-8")
    summary = {"module": "pcs.research.amd_full_clean_frozen_transfer", "version": "1.0", "data_source": "PCS_CANONICAL_DATA", "authoritative_input": str(IN.relative_to(ROOT)), "all_clean_testable_days_evaluated": True, "clean_testable_days": int(len(x)), "testable_day_min": str(x.date.min().date()), "testable_day_max": str(x.date.max().date()), "authoritative_dataset_window": {"start": "2020-01-02", "end": "2026-08-18"}, "2026_included": True, "2026_previously_exposed_for_diagnostics": True, "amd_specific_prefilter_added": False, "strategies": records, "controls": {"strategy_definitions_changed": False, "thresholds_changed": False, "production_rules_changed": False, "final_oos_touched": False}}
    (OUT / "full_scan_definitions_and_funnel.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
