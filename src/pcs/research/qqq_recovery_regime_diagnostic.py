"""Descriptive QQQ recovery-family broader-trend diagnostic.

Reads existing canonical TRAIN and validation artifacts only.  It does not
create a filter, tune a cutoff, or alter any strategy definition.
"""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "research_outputs/pcs_strategy_library"
TRAIN = ROOT / "research_outputs/qqq_entry_discovery_agent_v1/artifacts/controlled_reset_independent_episode_ledger.csv"
VAL = ROOT / "research_outputs/qqq_frozen_validation_20260824"

def run(output_dir=OUT):
    train = pd.read_csv(TRAIN)
    train["entry_date"] = pd.to_datetime(train["entry_date"])
    train["period"] = train.entry_date.dt.year.map({2022:"2022_TRAIN", 2023:"2023_TRAIN"}).fillna(train.entry_date.dt.year.astype(str))
    train["strategy"] = "CONTROLLED_RESET"
    cols = ["strategy","episode_id","entry_date","exit_date","period","realized_pnl","outcome_class","close","sma20","sma50","sma200","atr14","ret5","ret10","ret20","drawdown60","realized_vol20","volume_ratio20","close_sma50_atr","close_sma200_atr","above_sma50","above_sma200"]
    rows = train[[c for c in cols if c in train]].copy()
    rows["source_artifact"] = str(TRAIN.relative_to(ROOT))
    # Validation lifecycle files intentionally have no PIT feature columns;
    # retain them as lifecycle-only rows with explicit missing feature values.
    for name, label in [("QQQ_CONTROLLED_RESET_lifecycle.parquet","CONTROLLED_RESET"),("H006_RECOVERY_STABILIZATION_lifecycle.parquet","RECOVERY_STABILIZATION_AFTER_RESET"),("QQQ_SMA50_RECLAIM_AFTER_WEAKNESS_lifecycle.parquet","SMA50_RECLAIM_AFTER_WEAKNESS")]:
        d = pd.read_parquet(VAL / name)
        d = d.rename(columns={"pnl":"realized_pnl","date":"entry_date"})
        d["entry_date"] = pd.to_datetime(d.entry_date); d["exit_date"] = pd.to_datetime(d.exit_date); d["strategy"] = label; d["period"] = "2026_VALIDATION"
        for c in cols:
            if c not in d: d[c] = pd.NA
        d["source_artifact"] = str((VAL/name).relative_to(ROOT)); rows = pd.concat([rows, d[cols+['source_artifact']]], ignore_index=True)
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out / "qqq_recovery_regime_episode_diagnostic.csv", index=False)
    summary = {"module":"pcs.research.qqq_recovery_regime_diagnostic","version":"v1","status":"DESCRIPTIVE_ONLY","data_source":"PCS_CANONICAL_DATA_ARTIFACTS","strategies":["CONTROLLED_RESET","RECOVERY_STABILIZATION_AFTER_RESET","SMA50_RECLAIM_AFTER_WEAKNESS"],"periods_present":sorted(rows.period.dropna().unique().tolist()),"periods_missing_episode_features":["2024_HISTORICAL_HOLDOUT","2025_HISTORICAL_HOLDOUT"],"feature_availability":{"train_pit_features":True,"validation_pit_features":False},"findings":{"fact":["2022 TRAIN controlled-reset losses are concentrated in the existing TRAIN episode ledger.","2026 validation controlled-reset and recovery-stabilization lifecycle rows are negative while SMA50 reclaim rows are positive.","Validation lifecycle artifacts do not contain entry-date PIT feature columns."],"observed_pattern":["The available weak-period rows show early-recovery lifecycle losses and positive reclaim outcomes, but the validation sample is small."],"hypothesis":["Broader structural trend health may determine whether early recovery confirmation is sufficient."],"insufficient_evidence":["No episode-level 2024/2025 PIT feature ledger was found in the supplied authoritative artifacts.","No repeated cross-period test is authorized at this diagnostic stage."]},"controls":{"strategy_definitions_changed":False,"thresholds_changed":False,"lifecycle_changed":False,"production_rules_changed":False,"final_oos_touched":False}}
    (out / "qqq_recovery_regime_diagnostic_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = """# QQQ Recovery Family — Broader-Trend Diagnostic\n\n## Scope\n\nThis is a descriptive diagnostic using existing canonical TRAIN and 2026 validation artifacts. It does not create a regime filter or change any strategy.\n\n## FACT\n\n- The available TRAIN ledger contains PIT entry features for Controlled Reset through 2023.\n- The available 2026 validation lifecycle artifacts contain lifecycle outcomes but not PIT feature columns.\n- 2026 validation results are negative for Controlled Reset and Recovery Stabilization, while the two available SMA50 Reclaim trades are profitable.\n- No authoritative episode-level 2024–2025 PIT feature ledger was available in the inspected artifacts.\n\n## OBSERVED PATTERN\n\nThe available weak-period evidence is consistent with early-recovery entries being vulnerable while SMA50 reclaim entries can remain positive. The sample is too small to establish a repeated regime distinction.\n\n## HYPOTHESIS\n\nBroader structural trend health may determine whether early recovery confirmation is sufficient; structurally weak conditions may require SMA50 reclaim confirmation. This remains a research hypothesis, not a rule.\n\n## INSUFFICIENT EVIDENCE\n\nThe requested 2024–2025 episode-level feature comparison cannot be completed from the currently available authoritative artifacts without reconstructing or importing additional approved artifacts. Losses occurring before later SMA50 reclaim cannot be established from the present validation lifecycle-only files.\n\n## Safety\n\n`STRATEGY_DEFINITIONS_CHANGED = NO`  \n`THRESHOLDS_CHANGED = NO`  \n`LIFECYCLE_CHANGED = NO`  \n`PRODUCTION_RULES_CHANGED = NO`  \n`FINAL_OOS_TOUCHED = NO`\n"""
    (out / "qqq_recovery_regime_diagnostic_report.md").write_text(report, encoding="utf-8")
    return summary

if __name__ == "__main__": print(json.dumps(run(), indent=2))
